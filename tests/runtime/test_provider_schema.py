from pathlib import Path

import pytest

from sftpwarden.config import ProviderConfig, ProviderType, default_project_config, write_config
from sftpwarden.providers import SQLiteProvider
from sftpwarden.runtime.core import load_runtime_inputs
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea"


def test_t11_runtime_honors_v1_schema_without_migration(
    tmp_path: Path, applied_runtime_config
) -> None:
    database = tmp_path / "users.sqlite"
    config = default_project_config("schema", ProviderType.SQLITE)
    config.provider = ProviderConfig(type=ProviderType.SQLITE, path=str(database), user_schema=1)
    config.server.state_dir = str(tmp_path / "state")
    SQLiteProvider(config=config.provider, path=database).write(
        ProviderUsers(schema_version=1, users=[SFTPUser(username="alice", public_keys=[KEY])])
    )
    settings = tmp_path / "config.yaml"
    write_config(settings, config)
    applied_runtime_config(settings)
    before = database.read_bytes()
    _, users, _ = load_runtime_inputs(settings)
    assert users.schema_version == 1
    assert users.users[0].public_keys == [KEY]
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "provider_type",
    [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE],
)
def test_runtime_rejects_newer_data_without_mutating_applied_state(
    tmp_path,
    applied_runtime_config,
    provider_type,
):
    from sftpwarden.providers import build_provider
    from sftpwarden.users.schemas import migrate_provider_users

    path = tmp_path / f"users.{provider_type.value}"
    config = default_project_config("schema", provider_type, user_schema=1)
    config.provider.path = str(path)
    config.server.state_dir = str(tmp_path / "state")
    provider = build_provider(provider_type, path=path, user_schema=1)
    initial = ProviderUsers(schema_version=1, users=[SFTPUser(username="alice", public_keys=[KEY])])
    provider.write(initial)
    settings = tmp_path / "config.yaml"
    write_config(settings, config)
    applied_runtime_config(settings)
    migrated = migrate_provider_users(initial, to_version=2)
    migrated.users[0].keys[0].disabled = True
    provider.replace_if_unchanged(initial, migrated)
    before = path.read_bytes()
    with pytest.raises(ProviderError, match="differs"):
        load_runtime_inputs(settings)
    assert path.read_bytes() == before
    assert not (tmp_path / "state" / "state.json").exists()
