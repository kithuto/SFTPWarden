from __future__ import annotations

import shlex
from pathlib import Path

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.utils.paths import expand_path


def uses_default_ssh_identity(ssh_key: str | None) -> bool:
    """Return whether SSH should use the host default identity.

    Parameters
    ----------
    ssh_key
        SSH key setting from a remote context.

    Returns
    -------
    bool
        ``True`` when no explicit key should be passed to SSH.
    """
    return ssh_key is None or ssh_key.strip().lower() == "default"


def explicit_ssh_key_path(ssh_key: str | None) -> Path | None:
    """Resolve an explicit SSH key path.

    Parameters
    ----------
    ssh_key
        SSH key setting from a remote context.

    Returns
    -------
    Path | None
        Expanded key path, or ``None`` when the default SSH identity is used.
    """
    if uses_default_ssh_identity(ssh_key):
        return None
    return expand_path(ssh_key or "")


def ssh_base_command(remote: RemoteEndpoint, *, destination: bool = True) -> list[str]:
    """Build a safe SSH command argument list.

    Parameters
    ----------
    remote
        Remote endpoint to connect to.
    destination
        Whether to append ``user@host`` to the command.

    Returns
    -------
    list[str]
        SSH command arguments.
    """
    command = [
        "ssh",
        "-p",
        str(remote.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    key_path = explicit_ssh_key_path(remote.ssh_key)
    if key_path is not None:
        command.extend(["-i", str(key_path)])
    if destination:
        command.append(f"{remote.user}@{remote.host}")
    return command


def rsync_ssh_transport(remote: RemoteEndpoint) -> str:
    """Build the escaped SSH transport string used by ``rsync -e``.

    Parameters
    ----------
    remote
        Remote endpoint used for rsync transport.

    Returns
    -------
    str
        Shell-escaped SSH transport command.
    """
    return shlex.join(ssh_base_command(remote, destination=False))


def scp_upload_command(remote: RemoteEndpoint, local_path: Path, remote_path: str) -> list[str]:
    """Build a safe scp upload command for one file.

    Parameters
    ----------
    remote
        Remote endpoint used for the upload.
    local_path
        Local file to upload.
    remote_path
        Remote destination path.

    Returns
    -------
    list[str]
        SCP command arguments.
    """
    command = [
        "scp",
        "-P",
        str(remote.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    key_path = explicit_ssh_key_path(remote.ssh_key)
    if key_path is not None:
        command.extend(["-i", str(key_path)])
    command.extend([str(local_path), f"{remote.user}@{remote.host}:{remote_path}"])
    return command
