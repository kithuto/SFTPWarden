import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from sftpwarden.config import (
    ProviderType,
    default_project_config,
    load_config,
    provider_local_path,
    write_config,
)
from sftpwarden.providers import provider_for_config
from sftpwarden.services import provider_layout as migration
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


def setup_provider(root: Path, kind: ProviderType) -> tuple[Path, Path]:
    config = default_project_config("migration", kind)
    config_path = root / "sftpwarden.yaml"
    write_config(config_path, config)
    path = provider_local_path(root, config)
    provider_for_config(config.provider, path).write(
        ProviderUsers(schema_version=2, users=[SFTPUser(username="alice")])
    )
    return config_path, path


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_migration_preserves_data_and_old_copy_and_is_idempotent(
    tmp_path: Path, kind: ProviderType
) -> None:
    config_path, source = setup_provider(tmp_path, kind)
    original_config = config_path.read_bytes()
    original_provider = source.read_bytes()
    config = load_config(config_path)
    layout = migration.provider_layout(tmp_path, config)
    assert layout is not None and layout.needs_migration
    assert not layout.destination.exists()
    assert not (tmp_path / ".sftpwarden").exists()
    result = migration.migrate_provider_layout(tmp_path, config_path)
    destination = provider_local_path(tmp_path, result)
    assert destination == tmp_path / "provider" / source.name
    assert result.provider.path == f"/etc/sftpwarden/provider-data/{source.name}"
    assert source.read_bytes() == original_provider
    assert provider_for_config(result.provider, destination).read().users[0].username == "alice"
    assert (tmp_path / ".sftpwarden/provider-migration/config.yaml").read_bytes() == original_config
    assert migration.migration_marker(source).exists()
    with pytest.raises(ProviderError, match="successful deploy"):
        provider_for_config(result.provider, destination).upsert_user(SFTPUser(username="bob"))
    migration.complete_provider_layout(tmp_path, result)
    with pytest.raises(ProviderError, match="was migrated"):
        provider_for_config(config.provider, source).upsert_user(SFTPUser(username="bob"))
    provider_for_config(result.provider, destination).upsert_user(SFTPUser(username="bob"))
    committed = destination.read_bytes()
    migration.migrate_provider_layout(tmp_path, config_path)
    assert destination.read_bytes() == committed
    assert source.read_bytes() == original_provider


def test_sqlite_migration_includes_committed_wal(tmp_path: Path) -> None:
    config_path, source = setup_provider(tmp_path, ProviderType.SQLITE)
    with closing(sqlite3.connect(source)) as writer, writer:
        writer.execute("pragma journal_mode=wal")
        writer.execute("update sftp_users set comment='committed in WAL' where username='alice'")
        writer.commit()
        assert source.with_name(f"{source.name}-wal").exists()
        result = migration.migrate_provider_layout(tmp_path, config_path)
    user = (
        provider_for_config(result.provider, provider_local_path(tmp_path, result)).read().users[0]
    )
    assert user.comment == "committed in WAL"


def test_interrupted_config_commit_can_resume_without_losing_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, source = setup_provider(tmp_path, ProviderType.YAML)
    original = config_path.read_bytes()
    publish = migration.atomic_bytes

    def fail_config(path: Path, content: bytes) -> None:
        if path == config_path:
            raise OSError("Injected config publication failure")
        publish(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(migration, "atomic_bytes", fail_config)
        with pytest.raises(OSError):
            migration.migrate_provider_layout(tmp_path, config_path)
    assert config_path.read_bytes() == original
    assert (tmp_path / "provider" / source.name).read_bytes() == source.read_bytes()
    result = migration.migrate_provider_layout(tmp_path, config_path)
    assert provider_local_path(tmp_path, result).parent == tmp_path / "provider"


def test_migration_refuses_occupied_directory_and_invalid_source(tmp_path: Path) -> None:
    config_path, source = setup_provider(tmp_path, ProviderType.YAML)
    original = config_path.read_bytes()
    directory = tmp_path / "provider"
    directory.mkdir()
    unrelated = directory / "unrelated-secret"
    unrelated.write_text("synthetic")
    with pytest.raises(ProviderError, match="unrelated"):
        migration.migrate_provider_layout(tmp_path, config_path)
    assert config_path.read_bytes() == original
    unrelated.unlink()
    (directory / source.name).write_text("occupied")
    with pytest.raises(ProviderError, match="already exists"):
        migration.migrate_provider_layout(tmp_path, config_path)
    (directory / source.name).unlink()
    source.write_bytes(b"")
    with pytest.raises(ProviderError):
        migration.migrate_provider_layout(tmp_path, config_path)
    assert not (directory / source.name).exists()
    assert config_path.read_bytes() == original


@pytest.mark.parametrize("changed", ["source", "target", "journal", "backup"])
def test_interrupted_migration_refuses_conflicting_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    config_path, source = setup_provider(tmp_path, ProviderType.YAML)
    original = config_path.read_bytes()
    publish = migration.atomic_bytes

    def interrupt(path: Path, content: bytes) -> None:
        if path == config_path:
            raise OSError("Injected interruption")
        publish(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(migration, "atomic_bytes", interrupt)
        with pytest.raises(OSError):
            migration.migrate_provider_layout(tmp_path, config_path)
    paths = {
        "source": source,
        "target": tmp_path / "provider" / source.name,
        "journal": tmp_path / ".sftpwarden/provider-migration/journal.json",
        "backup": tmp_path / ".sftpwarden/provider-migration/provider",
    }
    conflict = paths[changed]
    conflict.write_text("users: []\n")
    with pytest.raises(ProviderError):
        migration.migrate_provider_layout(tmp_path, config_path)
    assert conflict.read_text() == "users: []\n"
    assert config_path.read_bytes() == original
