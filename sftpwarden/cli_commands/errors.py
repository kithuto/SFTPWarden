"""CLI error conversion and global Typer exception handling."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

import typer
import yaml
from pydantic import ValidationError

from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError

F = TypeVar("F", bound=Callable[..., Any])
DEBUG_ENV_VALUES = {"1", "true", "yes", "on"}


def handle_error(exc: SFTPWardenError) -> None:
    """Print a domain error and exit the CLI.

    Parameters
    ----------
    exc
        Application error with user-facing message and optional suggestion.
    """
    console.print(f"[bold red]Error:[/bold red] {exc.message}")
    if exc.suggestion:
        console.print(f"[bold yellow]Fix:[/bold yellow] {exc.suggestion}")
    raise typer.Exit(1)


def cli_error_from_exception(exc: Exception) -> SFTPWardenError:
    """Convert unexpected implementation errors into CLI-safe messages.

    Parameters
    ----------
    exc
        Exception raised while executing a CLI callback.

    Returns
    -------
    SFTPWardenError
        User-facing error with a concise remediation hint.
    """
    if isinstance(exc, SFTPWardenError):
        return exc
    if isinstance(exc, ValidationError):
        replicas_error = _kubernetes_replicas_error(exc)
        if replicas_error:
            return replicas_error
        return SFTPWardenError(
            f"Invalid data or configuration: {_validation_error_summary(exc)}",
            suggestion="Check the command values and SFTPWarden configuration files.",
        )
    if isinstance(exc, json.JSONDecodeError):
        return SFTPWardenError(
            f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.",
            suggestion="Fix the JSON file and try again.",
        )
    if isinstance(exc, tomllib.TOMLDecodeError):
        return SFTPWardenError(
            f"Invalid TOML: {exc}",
            suggestion="Fix the TOML file and try again.",
        )
    if isinstance(exc, yaml.YAMLError):
        return SFTPWardenError(
            f"Invalid YAML: {exc}",
            suggestion="Fix the YAML file and try again.",
        )
    if isinstance(exc, subprocess.TimeoutExpired):
        return SFTPWardenError(
            f"Command timed out: {_command_text(exc.cmd)}",
            suggestion="Check the external command, network connection, and remote host.",
        )
    if isinstance(exc, subprocess.CalledProcessError):
        details = exc.stderr or exc.stdout or str(exc)
        return SFTPWardenError(
            f"Command failed: {_command_text(exc.cmd)}. {details}",
            suggestion="Review the command output above and fix the failing external dependency.",
        )
    if isinstance(exc, FileNotFoundError):
        target = exc.filename or str(exc)
        return SFTPWardenError(
            f"File not found: {target}",
            suggestion="Check the path and run the command again.",
        )
    if isinstance(exc, PermissionError):
        target = exc.filename or str(exc)
        return SFTPWardenError(
            f"Permission denied: {target}",
            suggestion="Check file ownership, permissions, or run the required privileged step.",
        )
    if isinstance(exc, IsADirectoryError):
        target = exc.filename or str(exc)
        return SFTPWardenError(
            f"Expected a file but found a directory: {target}",
            suggestion="Pass a file path, not a directory path.",
        )
    if isinstance(exc, NotADirectoryError):
        target = exc.filename or str(exc)
        return SFTPWardenError(
            f"Expected a directory path but found a file component: {target}",
            suggestion="Check the parent directory path and try again.",
        )
    if isinstance(exc, UnicodeDecodeError):
        return SFTPWardenError(
            f"Could not read text as UTF-8: {exc}",
            suggestion="Save the file as UTF-8 and try again.",
        )
    if isinstance(exc, OSError):
        return SFTPWardenError(
            f"System error: {exc}",
            suggestion="Check the filesystem, permissions, and external command availability.",
        )
    if isinstance(exc, ValueError):
        return SFTPWardenError(
            f"Invalid value: {exc}",
            suggestion="Check the provided value and try again.",
        )
    if isinstance(exc, KeyError):
        return SFTPWardenError(
            f"Missing key: {_missing_key_text(exc)}",
            suggestion="Check the configuration file and required fields.",
        )
    return SFTPWardenError(
        f"Unexpected error: {type(exc).__name__}: {exc}",
        suggestion="Run again with SFTPWARDEN_DEBUG=1 to show the traceback, or open an issue.",
    )


def guard_cli_callback(callback: F | None) -> F | None:
    """Wrap a Typer callback so CLI users never see raw tracebacks.

    Parameters
    ----------
    callback
        Typer command or callback function.

    Returns
    -------
    Callable[..., Any] | None
        Guarded callback, preserving Typer's original signature metadata.
    """
    if callback is None or getattr(callback, "__sftpwarden_guarded__", False):
        return callback

    guarded_callback: F = callback

    @wraps(guarded_callback)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return guarded_callback(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except SFTPWardenError as exc:
            handle_error(exc)
        except Exception as exc:  # noqa: BLE001
            if _debug_enabled():
                raise
            handle_error(cli_error_from_exception(exc))

    wrapped.__sftpwarden_guarded__ = True  # type: ignore[attr-defined]
    return cast(F, wrapped)


def install_cli_error_handlers(typer_app: typer.Typer) -> None:
    """Install guarded error handling on an app and all registered sub-apps.

    Parameters
    ----------
    typer_app
        Root or nested Typer application.
    """
    callback_info = getattr(typer_app, "registered_callback", None)
    if callback_info is not None:
        callback_info.callback = guard_cli_callback(callback_info.callback)

    for command_info in getattr(typer_app, "registered_commands", []):
        command_info.callback = guard_cli_callback(command_info.callback)

    for group_info in getattr(typer_app, "registered_groups", []):
        install_cli_error_handlers(group_info.typer_instance)


def _debug_enabled() -> bool:
    """Return whether raw CLI tracebacks are enabled."""
    return os.environ.get("SFTPWARDEN_DEBUG", "").strip().lower() in DEBUG_ENV_VALUES


def _validation_error_summary(exc: ValidationError) -> str:
    """Return a concise summary of the first Pydantic validation error."""
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "value"
    message = first.get("msg", str(exc))
    return f"{location}: {message}"


def _kubernetes_replicas_error(exc: ValidationError) -> SFTPWardenError | None:
    """Translate the unsupported Kubernetes replica validation error."""
    for error in exc.errors():
        message = str(error.get("msg", ""))
        if "Kubernetes replicas > 1 are not supported yet." in message:
            return SFTPWardenError(
                message.removeprefix("Value error, "),
                suggestion="Set kubernetes.replicas to 1 for now.",
            )
    return None


def _command_text(command: Any) -> str:
    """Render an arbitrary command value as readable text."""
    if isinstance(command, str):
        return command
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command)


def _missing_key_text(exc: KeyError) -> str:
    """Return a KeyError value without Python's extra quoting."""
    if exc.args:
        return str(exc.args[0])
    return str(exc)
