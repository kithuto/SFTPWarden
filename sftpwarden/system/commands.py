from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from sftpwarden.utils.errors import SFTPWardenError


@dataclass(frozen=True)
class CommandResult:
    """Result returned by an external command.

    Attributes
    ----------
    args
        Command arguments that were executed.
    returncode
        Process return code.
    stdout
        Captured standard output.
    stderr
        Captured standard error.
    """

    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Return the most useful captured output.

        Returns
        -------
        str
            Standard error when present, otherwise standard output.
        """
        return (self.stderr or self.stdout).strip()


def command_text(args: list[str]) -> str:
    """Render a command for display.

    Parameters
    ----------
    args
        Command arguments.

    Returns
    -------
    str
        Shell-escaped representation suitable for logs and dry-runs.
    """
    return shlex.join(args)


def run(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
) -> CommandResult:
    """Run an external command without raising on failure.

    Parameters
    ----------
    args
        Command arguments to execute.
    cwd
        Optional working directory.
    timeout
        Optional command timeout in seconds.
    capture_output
        Whether to capture stdout and stderr.

    Returns
    -------
    CommandResult
        Captured command result.
    """
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            text=True,
            capture_output=capture_output,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            args=args,
            returncode=127,
            stdout="",
            stderr=f"Executable not found: {exc.filename}",
        )
    return CommandResult(
        args=args,
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


def run_checked(
    args: list[str],
    *,
    cwd: str | None = None,
    error_type: type[SFTPWardenError],
    message: str,
    fallback_suggestion: str,
    timeout: float | None = None,
    capture_output: bool = True,
) -> CommandResult:
    """Run an external command and raise a typed SFTPWarden error on failure.

    Parameters
    ----------
    args
        Command arguments to execute.
    cwd
        Optional working directory.
    error_type
        Error class to raise when the command fails.
    message
        Error message for failures.
    fallback_suggestion
        Suggestion used when the command does not provide output.
    timeout
        Optional command timeout in seconds.
    capture_output
        Whether to capture stdout and stderr.

    Returns
    -------
    CommandResult
        Successful command result.

    Raises
    ------
    SFTPWardenError
        Raised as ``error_type`` when the command exits with a non-zero code.
    """
    command_result = run(
        args,
        cwd=cwd,
        capture_output=capture_output,
        timeout=timeout,
    )
    if command_result.returncode != 0:
        raise error_type(
            message,
            suggestion=command_result.output or fallback_suggestion,
        )
    return command_result
