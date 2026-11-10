"""External restoration must protect the selected database against stale or redirected writes."""

import json
from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.render.compose import write_compose
from sftpwarden.services import backup, restore_existing
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError, RuntimeError


@pytest.fixture
def external_target(tmp_path, monkeypatch, memory_provider_factory):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev", ProviderType.MYSQL, dsn="mysql://example.invalid/source")
    write_config(root / "sftpwarden.yaml", config)
    write_compose(config, root, allow_local_build=False)
    entry = local_context("dev", root, ProviderType.MYSQL)
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))
    provider = memory_provider_factory(
        ProviderUsers(
            schema_version=2,
            users=[SFTPUser(username="alice", disabled=True)],
        )
    )
    monkeypatch.setattr(backup, "provider_from_config", lambda *_args: provider)
    monkeypatch.setattr(restore_existing, "provider_from_config", lambda *_args: provider)
    archive = tmp_path / "source.tar.gz"
    backup.create_backup(context_name="dev", output=str(archive))
    return root, config, provider, archive


@pytest.mark.parametrize("dry_run", [True, False])
def test_external_restore_rejects_archive_database_retargeting(external_target, dry_run):
    root, config, provider, archive = external_target
    config.provider.dsn = "mysql://example.invalid/destination"
    write_config(root / "sftpwarden.yaml", config)
    before = (root / "sftpwarden.yaml").read_bytes()
    with pytest.raises(RuntimeError, match="destination"):
        backup.restore_backup(context_name="dev", backup_path=str(archive), dry_run=dry_run)
    assert not provider.writes
    assert (root / "sftpwarden.yaml").read_bytes() == before


def test_external_restore_rejects_concurrent_policy_edit(external_target, monkeypatch):
    _root, _config, provider, archive = external_target
    concurrent = ProviderUsers(schema_version=2, users=[SFTPUser(username="later", disabled=True)])
    replace = provider.replace_if_unchanged

    def conflicting_replace(expected, replacement):
        provider.users = concurrent
        replace(expected, replacement)

    monkeypatch.setattr(provider, "replace_if_unchanged", conflicting_replace)
    with pytest.raises((RuntimeError, ProviderError)):
        backup.restore_backup(context_name="dev", backup_path=str(archive))
    assert provider.read() == concurrent


def test_external_commit_followed_by_file_failure_keeps_both_recovery_steps(
    external_target,
    monkeypatch,
):
    root, config, provider, archive = external_target
    previous = ProviderUsers(schema_version=2, users=[SFTPUser(username="before", disabled=True)])
    provider.users = previous
    config.project.description = "configuration before restoration"
    write_config(root / "sftpwarden.yaml", config)
    before_config = (root / "sftpwarden.yaml").read_bytes()
    publish = restore_existing.publish_existing_files

    def fail_copy(*_args, **_kwargs):
        raise OSError("file publication failed")

    def interrupt(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(restore_existing.shutil, "copyfileobj", fail_copy)
            return publish(*args, **kwargs)

    monkeypatch.setattr(restore_existing, "publish_existing_files", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        backup.restore_backup(context_name="dev", backup_path=str(archive))
    assert [user.username for user in provider.read().users] == ["alice"]
    assert (root / "sftpwarden.yaml").read_bytes() == before_config
    journal = json.loads((root / restore_existing.JOURNAL).read_text())
    assert journal["completed"] == [restore_existing.PROVIDER_STEP]
    assert "sftpwarden.yaml" in journal["pending"]
    assert journal["external_provider"] is True
    assert restore_existing.verify_safety_archive(Path(journal["safety_backup"])).users == previous
    assert "provider import" in journal["recovery_action"]
    assert "--root" not in journal["recovery_action"]
    with pytest.raises(RuntimeError, match="interrupted restore"):
        backup.restore_backup(context_name="dev", backup_path=str(archive), dry_run=True)


def test_external_step_does_not_alias_a_custom_config_filename(external_target):
    root, config, provider, archive = external_target
    custom = root / "external-provider"
    (root / "sftpwarden.yaml").rename(custom)
    entry = local_context("dev", root, ProviderType.MYSQL).model_copy(
        update={"config": str(custom)},
    )
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))
    backup.create_backup(context_name="dev", output=str(archive))
    config.project.description = "changed configuration"
    write_config(custom, config)
    provider.users = ProviderUsers(schema_version=2, users=[])
    backup.restore_backup(context_name="dev", backup_path=str(archive))
    assert "changed configuration" not in custom.read_text()
    assert [user.username for user in provider.read().users] == ["alice"]
    journal = json.loads((root / restore_existing.JOURNAL).read_text())
    assert "external-provider" in journal["completed"]
    assert restore_existing.PROVIDER_STEP in journal["completed"]
