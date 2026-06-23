from __future__ import annotations

import json
import subprocess
import tomllib
from collections.abc import Callable
from typing import Any

import pytest
import typer
import yaml
from pydantic import BaseModel, ValidationError
from typer.testing import CliRunner

import sftpwarden.cli_commands.config as config_commands
import sftpwarden.cli_commands.context as context_commands
import sftpwarden.cli_commands.core as core_commands
from sftpwarden.cli import app
from sftpwarden.cli_commands.errors import (
    cli_error_from_exception,
    guard_cli_callback,
)
from sftpwarden.utils.errors import SFTPWardenError


class _ValidationSample(BaseModel):
    count: int


def _capture_exception(factory: Callable[[], Any], expected: type[Exception]) -> Exception:
    with pytest.raises(expected) as raised:
        factory()
    return raised.value


def test_all_registered_cli_callbacks_are_guarded() -> None:
    """Every public Typer command is protected by the common CLI error boundary."""
    callbacks: list[tuple[str, Callable[..., Any]]] = []
    root_callback = getattr(app.registered_callback, "callback", None)
    if root_callback is not None:
        callbacks.append(("<root>", root_callback))
    for command in app.registered_commands:
        callbacks.append((command.name or command.callback.__name__, command.callback))
    for group in app.registered_groups:
        group_callback = getattr(group.typer_instance.registered_callback, "callback", None)
        if group_callback is not None:
            callbacks.append((f"{group.name} <callback>", group_callback))
        for command in group.typer_instance.registered_commands:
            callbacks.append(
                (
                    f"{group.name} {command.name or command.callback.__name__}",
                    command.callback,
                )
            )

    unguarded = [name for name, callback in callbacks if not _is_guarded(callback)]

    assert len(callbacks) >= 100
    assert unguarded == []


def _is_guarded(callback: Callable[..., Any]) -> bool:
    return bool(getattr(callback, "__sftpwarden_guarded__", False))


def test_unexpected_cli_exceptions_are_rendered_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected command errors are still user-facing CLI errors."""
    monkeypatch.setattr(
        core_commands,
        "resolve_context",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad context value")),
    )

    result = CliRunner().invoke(app, ["info"])

    assert result.exit_code == 1
    assert "Error: Invalid value: bad context value" in result.output
    assert "Fix: Check the provided value and try again." in result.output
    assert "Traceback" not in result.output


def test_unexpected_subcommand_exceptions_are_rendered_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested Typer commands also use the common error boundary."""
    monkeypatch.setattr(context_commands, "require_initialized_context", lambda: None)
    monkeypatch.setattr(
        context_commands,
        "load_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("registry failed")),
    )

    result = CliRunner().invoke(app, ["context", "ls"])

    assert result.exit_code == 1
    assert "Error: Unexpected error: RuntimeError: registry failed" in result.output
    assert "Fix: Run again with SFTPWARDEN_DEBUG=1" in result.output
    assert "Traceback" not in result.output


def test_dynamic_command_exceptions_are_rendered_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dynamically registered field commands are guarded too."""
    monkeypatch.setattr(
        config_commands,
        "update_project_config_value",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dynamic failed")),
    )

    result = CliRunner().invoke(app, ["config", "server.port", "2200"])

    assert result.exit_code == 1
    assert "Error: Unexpected error: RuntimeError: dynamic failed" in result.output
    assert "Traceback" not in result.output


def test_manual_value_error_handlers_use_cli_error_translator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callbacks that catch ValueError still use the shared CLI formatting."""
    monkeypatch.setattr(
        config_commands,
        "update_project_config_value",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("not a port")),
    )

    result = CliRunner().invoke(app, ["config", "server.port", "2200"])

    assert result.exit_code == 1
    assert "Error: Invalid value: not a port" in result.output
    assert "Fix: Check the provided value and try again." in result.output
    assert "Traceback" not in result.output


def test_debug_mode_re_raises_unexpected_cli_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SFTPWARDEN_DEBUG keeps tracebacks available for development."""
    monkeypatch.setenv("SFTPWARDEN_DEBUG", "1")
    monkeypatch.setattr(
        core_commands,
        "resolve_context",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("debug failure")),
    )

    with pytest.raises(RuntimeError, match="debug failure"):
        CliRunner().invoke(app, ["info"], catch_exceptions=False)


def test_guard_cli_callback_preserves_typer_control_flow() -> None:
    """Typer exits and aborts are not converted into user errors."""

    def exits() -> None:
        raise typer.Exit(3)

    def aborts() -> None:
        raise typer.Abort()

    guarded_exit = guard_cli_callback(exits)
    guarded_abort = guard_cli_callback(aborts)

    with pytest.raises(typer.Exit) as exit_error:
        assert guarded_exit is not None
        guarded_exit()
    with pytest.raises(typer.Abort):
        assert guarded_abort is not None
        guarded_abort()
    assert exit_error.value.exit_code == 3


def test_guard_cli_callback_handles_domain_errors_and_is_idempotent() -> None:
    """Domain errors keep their own message and guarded callbacks are stable."""

    def command() -> None:
        raise SFTPWardenError("domain failed", suggestion="Use a valid project.")

    guarded = guard_cli_callback(command)
    guarded_again = guard_cli_callback(guarded)

    assert guarded is guarded_again
    with pytest.raises(typer.Exit) as exit_error:
        assert guarded is not None
        guarded()
    assert exit_error.value.exit_code == 1
    assert guard_cli_callback(None) is None


def test_cli_error_from_exception_returns_existing_domain_error() -> None:
    """Domain errors are already safe for the CLI."""
    original = SFTPWardenError("already safe", suggestion="Nothing to convert.")

    assert cli_error_from_exception(original) is original


@pytest.mark.parametrize(
    ("exc", "message", "suggestion"),
    [
        (
            _capture_exception(lambda: _ValidationSample(count="bad"), ValidationError),
            "Invalid data or configuration: count:",
            "Check the command values and SFTPWarden configuration files.",
        ),
        (
            _capture_exception(lambda: json.loads("{"), json.JSONDecodeError),
            "Invalid JSON:",
            "Fix the JSON file and try again.",
        ),
        (
            _capture_exception(lambda: tomllib.loads("broken ="), tomllib.TOMLDecodeError),
            "Invalid TOML:",
            "Fix the TOML file and try again.",
        ),
        (
            _capture_exception(lambda: yaml.safe_load("users: ["), yaml.YAMLError),
            "Invalid YAML:",
            "Fix the YAML file and try again.",
        ),
        (
            subprocess.TimeoutExpired("ssh prod", timeout=1),
            "Command timed out: ssh prod",
            "Check the external command, network connection, and remote host.",
        ),
        (
            subprocess.CalledProcessError(
                1,
                ["docker", "compose", "up"],
                output="stdout",
                stderr="stderr",
            ),
            "Command failed: docker compose up. stderr",
            "Review the command output above and fix the failing external dependency.",
        ),
        (
            FileNotFoundError(2, "No such file", "/project/missing.yaml"),
            "File not found: /project/missing.yaml",
            "Check the path and run the command again.",
        ),
        (
            PermissionError(13, "Permission denied", "/root/sftpwarden.yaml"),
            "Permission denied: /root/sftpwarden.yaml",
            "Check file ownership, permissions, or run the required privileged step.",
        ),
        (
            IsADirectoryError(21, "Is a directory", "/project"),
            "Expected a file but found a directory: /project",
            "Pass a file path, not a directory path.",
        ),
        (
            NotADirectoryError(20, "Not a directory", "/project/users.yaml"),
            "Expected a directory path but found a file component: /project/users.yaml",
            "Check the parent directory path and try again.",
        ),
        (
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            "Could not read text as UTF-8:",
            "Save the file as UTF-8 and try again.",
        ),
        (
            OSError("disk full"),
            "System error: disk full",
            "Check the filesystem, permissions, and external command availability.",
        ),
        (
            ValueError("invalid port"),
            "Invalid value: invalid port",
            "Check the provided value and try again.",
        ),
        (
            KeyError("provider"),
            "Missing key: provider",
            "Check the configuration file and required fields.",
        ),
        (
            RuntimeError("unknown failure"),
            "Unexpected error: RuntimeError: unknown failure",
            "Run again with SFTPWARDEN_DEBUG=1 to show the traceback, or open an issue.",
        ),
    ],
)
def test_cli_error_from_exception_formats_common_errors(
    exc: Exception,
    message: str,
    suggestion: str,
) -> None:
    """Common implementation exceptions become actionable CLI errors."""
    error = cli_error_from_exception(exc)

    assert message in error.message
    assert error.suggestion == suggestion


def test_cli_error_from_exception_handles_sparse_exception_details() -> None:
    """Fallback helpers still produce readable messages with sparse exceptions."""
    timeout_error = cli_error_from_exception(subprocess.TimeoutExpired(object(), timeout=1))
    missing_key_error = cli_error_from_exception(KeyError())

    assert "Command timed out:" in timeout_error.message
    assert missing_key_error.message.startswith("Missing key:")
