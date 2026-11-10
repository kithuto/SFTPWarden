"""Recovery into an explicitly empty destination must not depend on the lost source."""

import os
import stat
from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.providers import provider_from_config
from sftpwarden.runtime.core import RuntimeState, RuntimeUserState
from sftpwarden.services.backup import create_backup, restore_backup, write_backup_archive
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("provider", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_fresh_restore_does_not_read_a_damaged_original(
    local_project_factory, provider, monkeypatch
):
    original_sync = os.fsync

    def writable_sync(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.write(descriptor, b"")  # Windows rejects fsync on read-only file descriptors.
        original_sync(descriptor)

    monkeypatch.setattr(os, "fsync", writable_sync)
    root, context = local_project_factory(provider=provider)
    source_config = load_config(context.config)
    source_provider = provider_from_config(root, source_config)
    source_provider.write(
        ProviderUsers(
            schema_version=source_config.provider.user_schema,
            users=[SFTPUser(username="alice", disabled=True, uid=2001, gid=2001)],
        )
    )
    expected = source_provider.read()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=context.name, output=str(archive))
    source_provider.path.unlink()
    destination = root.parent / "recovery"
    result = restore_backup(
        context_name=None,
        backup_path=str(archive),
        target_root=str(destination),
    )
    assert result.safety_backup is None
    assert result.target_root == destination
    assert result.target_state == "empty"
    restored_config = load_config(destination / "sftpwarden.yaml")
    assert provider_from_config(destination, restored_config).read() == expected
    assert not source_provider.path.exists()
    assert archive.exists()


def test_fresh_restore_dry_run_does_not_create_target(local_project_factory):
    root, context = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=context.name, output=str(archive))
    destination = root.parent / "recovery"
    result = restore_backup(
        context_name=None,
        backup_path=str(archive),
        target_root=str(destination),
        dry_run=True,
    )
    assert not destination.exists()
    assert result.safety_backup is None


def test_fresh_restore_refuses_unrelated_destination_content(local_project_factory):
    root, context = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=context.name, output=str(archive))
    destination = root.parent / "recovery"
    destination.mkdir()
    unrelated = destination / "unrelated.txt"
    unrelated.write_text("keep")
    with pytest.raises(RuntimeError, match="empty"):
        restore_backup(context_name=None, backup_path=str(archive), target_root=str(destination))
    assert list(destination.iterdir()) == [unrelated]
    assert unrelated.read_text() == "keep"


def test_failed_fresh_copy_leaves_destination_empty(local_project_factory, monkeypatch):
    import sftpwarden.services.restore_target as recovery

    root, context = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=context.name, output=str(archive))
    destination = root.parent / "recovery"
    destination.mkdir()

    def fail_copy(source: Path, target: Path):
        target.write_text("partial")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(recovery.shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="copy failure"):
        restore_backup(context_name=None, backup_path=str(archive), target_root=str(destination))
    assert not list(destination.iterdir())
    assert archive.exists()
    assert not list(destination.parent.glob(".sftpwarden-recovery-*"))


def test_fresh_restore_refuses_external_provider_without_connecting_to_it(
    local_project_factory,
    monkeypatch,
    memory_provider_factory,
):
    import sftpwarden.services.backup as backups

    root, context = local_project_factory()
    config = default_project_config(
        "dev", ProviderType.MYSQL, dsn="mysql://example.invalid/isolated"
    )
    write_config(Path(context.config), config)
    provider = memory_provider_factory(
        ProviderUsers(
            schema_version=config.provider.user_schema,
            users=[],
        )
    )
    monkeypatch.setattr(backups, "provider_from_config", lambda *_args: provider)
    archive = root.parent / "database.tar.gz"
    create_backup(context_name=context.name, output=str(archive))
    destination = root.parent / "recovery"
    with pytest.raises(RuntimeError, match="isolated external provider"):
        restore_backup(context_name=None, backup_path=str(archive), target_root=str(destination))
    assert not provider.writes
    assert not destination.exists()


def test_fresh_runtime_dry_run_validates_ownership_before_reporting_a_plan(
    local_project_factory,
    monkeypatch,
):
    root, _entry = local_project_factory()
    RuntimeState(users={"alice": RuntimeUserState(uid=2001, gid=2001)}).save(
        root / "state" / "state.json",
    )
    path = root / "data" / "alice" / "upload" / "file.txt"
    path.parent.mkdir(parents=True)
    path.write_text("private")
    archive = root.parent / "unmapped-owner.tar.gz"
    write_backup_archive(
        output_path=archive,
        root=root,
        entries=["sftpwarden.yaml", "users.yaml", "state", "data"],
        context_name="dev",
        provider_type="yaml",
        include_data=True,
        ownership=lambda path, name: (999, 999) if name.endswith("file.txt") else (0, 0),
    )
    destination = root.parent / "recovery"
    with pytest.raises(RuntimeError, match="ownership"):
        restore_backup(
            context_name=None,
            backup_path=str(archive),
            target_root=str(destination),
            include_data=True,
            dry_run=True,
        )
    assert not destination.exists()


def test_fresh_private_data_requires_retained_runtime_identity(local_project_factory):
    root, entry = local_project_factory()
    (root / "data").mkdir()
    (root / "data" / "private.txt").write_text("private")
    archive = root.parent / "no-identities.tar.gz"
    with pytest.raises(RuntimeError, match="maintenance"):
        create_backup(context_name=entry.name, output=str(archive), include_data=True)
    assert not archive.exists()
    # An older/incomplete archive must remain rejected even after capture is fixed.
    write_backup_archive(
        output_path=archive,
        root=root,
        entries=["sftpwarden.yaml", "users.yaml", "data"],
        context_name=entry.name,
        provider_type="yaml",
        include_data=True,
    )
    destination = root.parent / "recovery"
    with pytest.raises(RuntimeError, match="identity"):
        restore_backup(
            context_name=None,
            backup_path=str(archive),
            target_root=str(destination),
            include_data=True,
        )
    assert not destination.exists()
