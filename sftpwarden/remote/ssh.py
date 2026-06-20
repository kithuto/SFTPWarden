from __future__ import annotations

import shlex
from pathlib import Path

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.utils.paths import expand_path


def uses_default_ssh_identity(ssh_key: str | None) -> bool:
    return ssh_key is None or ssh_key.strip().lower() == "default"


def explicit_ssh_key_path(ssh_key: str | None) -> Path | None:
    if uses_default_ssh_identity(ssh_key):
        return None
    return expand_path(ssh_key or "")


def ssh_base_command(remote: RemoteEndpoint, *, destination: bool = True) -> list[str]:
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
    return shlex.join(ssh_base_command(remote, destination=False))
