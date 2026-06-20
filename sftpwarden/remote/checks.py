from __future__ import annotations

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.remote.ssh import uses_default_ssh_identity
from sftpwarden.system.commands import run_checked
from sftpwarden.utils.errors import ContextError


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
    run_checked(
        [*ssh_base_command(remote), remote_command],
        error_type=ContextError,
        message=f"Remote check failed: {remote_command}",
        fallback_suggestion="Verify SSH access.",
    )


def verify_remote_runtime_requirements(remote: RemoteEndpoint) -> None:
    run_remote_check(remote, "true")
    run_remote_check(remote, "docker compose version")
