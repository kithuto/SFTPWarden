from __future__ import annotations

from pathlib import Path

from sftpwarden.config import ProviderType
from sftpwarden.contexts import remote_context
from sftpwarden.remote.ssh import scp_upload_command


def test_scp_upload_command_includes_explicit_key(tmp_path: Path) -> None:
    key = tmp_path / "deploy_key"
    local_file = tmp_path / "users.yaml"
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="/opt/sftpwarden",
        remote_only=False,
        ssh_key=str(key),
        critical=True,
    )
    assert entry.remote is not None

    command = scp_upload_command(entry.remote, local_file, "/opt/sftpwarden/users.yaml")

    assert command[0] == "scp"
    assert command[command.index("-i") + 1] == str(key)
