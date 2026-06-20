from __future__ import annotations

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.system.commands import run_checked
from sftpwarden.utils.errors import ContextError


def run_remote_check(
    remote: RemoteEndpoint,
    remote_command: str,
    *,
    fallback_suggestion: str = "Verify SSH access.",
) -> None:
    """Run a remote shell check over SSH.

    Parameters
    ----------
    remote
        Remote endpoint to connect to.
    remote_command
        Shell command to execute remotely.
    fallback_suggestion
        Suggestion shown when the command fails without output.

    Raises
    ------
    ContextError
        Raised when SSH or the remote command fails.
    """
    run_checked(
        [*ssh_base_command(remote), remote_command],
        error_type=ContextError,
        message=f"Remote check failed: {remote_command}",
        fallback_suggestion=fallback_suggestion,
    )


def verify_remote_runtime_requirements(remote: RemoteEndpoint) -> None:
    """Verify that a remote host can run the SFTPWarden runtime.

    Parameters
    ----------
    remote
        Remote endpoint to inspect.

    Raises
    ------
    ContextError
        Raised when SSH access or Docker Compose is unavailable.
    """
    run_remote_check(remote, "true")
    run_remote_check(
        remote,
        "docker compose version",
        fallback_suggestion="Install Docker Compose v2 on the remote host and retry.",
    )
