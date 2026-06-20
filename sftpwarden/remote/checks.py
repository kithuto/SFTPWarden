from __future__ import annotations

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.system.commands import run_checked
from sftpwarden.utils.errors import ContextError


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
