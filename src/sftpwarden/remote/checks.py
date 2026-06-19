from __future__ import annotations

import subprocess

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.utils.errors import ContextError
from sftpwarden.remote.ssh import uses_default_ssh_identity


def ssh_base_command(remote: RemoteEndpoint) -> list[str]:
    command = [
        "ssh",
        "-p",
        str(remote.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    if not uses_default_ssh_identity(remote.ssh_key):
        command.extend(["-i", remote.ssh_key or ""])
    command.append(f"{remote.user}@{remote.host}")
    return command


def run_remote_check(remote: RemoteEndpoint, remote_command: str) -> None:
    result = subprocess.run(
        [*ssh_base_command(remote), remote_command],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ContextError(
            f"Remote check failed: {remote_command}",
            suggestion=(result.stderr or result.stdout or "Verify SSH access.").strip(),
        )


def verify_remote_runtime_requirements(remote: RemoteEndpoint) -> None:
    run_remote_check(remote, "true")
    run_remote_check(remote, "docker compose version")
