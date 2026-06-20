from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from sftpwarden.utils.errors import SFTPWardenError


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return (self.stderr or self.stdout).strip()


def command_text(args: list[str]) -> str:
    return shlex.join(args)


def run(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
) -> CommandResult:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=capture_output,
        timeout=timeout,
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
