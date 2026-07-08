from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command_name

from sftpwarden.cli import app
from sftpwarden.cli_commands.context import CONTEXT_FIELD_COMMANDS
from sftpwarden.utils.constants import PROJECT_CONFIG_PATHS

from .conftest import ReleaseCli, assert_failed, assert_no_traceback, assert_ok


def public_cli_commands_from_code() -> list[list[str]]:
    """Return the public command tree registered by the real Typer app."""
    commands: list[list[str]] = [[]]
    commands.extend(_collect_typer_commands(app))
    return commands


def _collect_typer_commands(typer_app: Any, prefix: list[str] | None = None) -> list[list[str]]:
    """Collect visible commands and groups from a Typer app instance."""
    prefix = prefix or []
    paths: list[list[str]] = []
    registered_commands = sorted(
        getattr(typer_app, "registered_commands", []),
        key=lambda command: _registered_command_name(command),
    )
    for command in registered_commands:
        if getattr(command, "hidden", False):
            continue
        name = _registered_command_name(command)
        path = [*prefix, name]
        paths.append(path)
    registered_groups = sorted(
        getattr(typer_app, "registered_groups", []),
        key=lambda group: _registered_group_name(group),
    )
    for group in registered_groups:
        if getattr(group, "hidden", False):
            continue
        name = _registered_group_name(group)
        path = [*prefix, name]
        paths.append(path)
        paths.extend(_collect_typer_commands(group.typer_instance, path))
    return paths


def _registered_command_name(command: Any) -> str:
    """Return the public CLI name for a Typer command registration."""
    explicit_name = getattr(command, "name", None)
    callback_name = getattr(getattr(command, "callback", None), "__name__", "")
    if explicit_name:
        return str(explicit_name)
    return get_command_name(callback_name)


def _registered_group_name(group: Any) -> str:
    """Return the public CLI name for a Typer group registration."""
    explicit_name = getattr(group, "name", None)
    if explicit_name:
        return str(explicit_name)
    callback_name = getattr(getattr(group, "callback", None), "__name__", "")
    return get_command_name(callback_name)


PUBLIC_HELP_COMMANDS = public_cli_commands_from_code()
PROJECT_CONFIG_PATH_SET = set(PROJECT_CONFIG_PATHS)
CONTEXT_FIELD_COMMAND_SET = set(CONTEXT_FIELD_COMMANDS)


@pytest.mark.release_validation
@pytest.mark.parametrize("command", PUBLIC_HELP_COMMANDS)
def test_public_commands_have_help(cli: ReleaseCli, command: list[str]) -> None:
    """Every public command registered in code should expose help without crashing."""
    result = cli.run(*command, "--help", timeout=30)

    assert_ok(result)
    assert "Usage:" in result.output


@pytest.mark.release_validation
def test_release_surface_is_discovered_from_code() -> None:
    """Release validation should follow the code-registered CLI, not documentation."""
    commands = {" ".join(command) for command in PUBLIC_HELP_COMMANDS}

    assert "user create" in commands
    assert "config project.name" in commands
    assert "context remote-root" in commands
    assert "helm upgrade" in commands
    assert "kube apply" in commands


@pytest.mark.release_validation
def test_version_and_unknown_command_are_controlled(cli: ReleaseCli) -> None:
    """Global version succeeds and bad commands fail without tracebacks."""
    version = cli.run("--version")
    unknown = cli.run("does-not-exist")

    assert_ok(version)
    assert "SFTPWarden" in version.output
    assert_failed(unknown, "No such command")


@pytest.mark.release_validation
def test_cli_reference_mentions_every_primary_command() -> None:
    """User docs should follow primary public commands discovered from code."""
    docs = Path("docs/cli-reference.md").read_text(encoding="utf-8")
    docs += "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(Path("docs/cli").glob("*.md"))
    )
    documented_commands = {
        " ".join(["sftpwarden", *command])
        for command in PUBLIC_HELP_COMMANDS
        if should_have_explicit_cli_reference_entry(command)
    }

    missing = sorted(command for command in documented_commands if command not in docs)

    assert not missing, f"CLI docs are missing: {missing}"


def should_have_explicit_cli_reference_entry(command: list[str]) -> bool:
    """Return whether a code-discovered command should be named explicitly in docs."""
    if not command or command[0] == "runtime":
        return False
    if command[0] == "config" and len(command) > 1 and command[1] in PROJECT_CONFIG_PATH_SET:
        return False
    return not (
        command[0] == "context" and len(command) > 1 and command[1] in CONTEXT_FIELD_COMMAND_SET
    )


@pytest.mark.release_validation
def test_root_help_does_not_emit_raw_exceptions(cli: ReleaseCli) -> None:
    """A plain invocation should show usage/help instead of internals."""
    result = cli.run(timeout=30)

    assert_no_traceback(result)
    assert "Usage:" in result.output or result.returncode == 0
