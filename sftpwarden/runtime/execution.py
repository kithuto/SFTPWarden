"""Bound reconciliation subprocesses and terminate their entire process group."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from sftpwarden.runtime.lifecycle import ATTEMPT_SECONDS, SHUTDOWN_SECONDS
from sftpwarden.utils.errors import RuntimeError


def stop_process(process: subprocess.Popen[str], *, grace: float = SHUTDOWN_SECONDS) -> None:
    """Terminate a private process group and reap its leader within a fixed grace."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace)
    # Descendants may survive their leader; always close this owned process group.
    try:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    except PermissionError:
        # Some POSIX hosts report EPERM while a terminated group is being reaped.
        # Do not treat that as success unless the child demonstrably exits.
        process.wait(timeout=SHUTDOWN_SECONDS)
    process.wait(timeout=SHUTDOWN_SECONDS)


def execute_pass(
    config_path: str | Path, *, force: bool = False, timeout: float = ATTEMPT_SECONDS
) -> str:
    """Apply policy with a wall-clock deadline, including provider and lock waits."""
    command = [sys.executable, "-m", "sftpwarden.runtime.worker", "--once", str(config_path)]
    if force:
        command.append("--force")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
        if process.returncode:
            raise RuntimeError("Runtime reconciliation failed; inspect the private runtime status.")
        return stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError("Runtime reconciliation exceeded its deadline.") from None
    finally:
        stop_process(process, grace=0)
