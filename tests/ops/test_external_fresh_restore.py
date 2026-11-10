"""Separate database recovery must retain proof and refuse ambiguous continuations."""

import json
import tarfile
from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.render.compose import write_compose
from sftpwarden.services import backup, restore_external
from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.services.restore_existing import verify_safety_archive
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.archives import extract_private_archive
from sftpwarden.utils.errors import RuntimeError


@pytest.fixture
def recovery(tmp_path, monkeypatch, memory_provider_factory):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    source = tmp_path / "source"
    connection = tmp_path / "connection"
    for root in (source, connection):
        root.mkdir(mode=0o700)
    config = default_project_config(
        "source", ProviderType.MYSQL, dsn="mysql://synthetic.invalid/source"
    )
    write_config(source / "sftpwarden.yaml", config)
    write_compose(config, source, allow_local_build=False)
    source_users = ProviderUsers(
        schema_version=2, users=[SFTPUser(username="alice", disabled=True)]
    )
    original = memory_provider_factory(source_users)
    replacement = memory_provider_factory(ProviderUsers(schema_version=2, users=[]))
    selected = config.model_copy(deep=True)
    selected.provider.table = "recovered_users"
    write_config(connection / "sftpwarden.yaml", selected)
    entries = {
        "source": local_context("source", source, ProviderType.MYSQL),
        "destination": local_context("destination", connection, ProviderType.MYSQL),
    }
    save_registry(ContextRegistry(default="source", contexts=entries))

    def provider(_root, current):
        return replacement if current.provider.table == "recovered_users" else original

    monkeypatch.setattr(backup, "provider_from_config", provider)
    monkeypatch.setattr(restore_external, "provider_from_config", provider)
    archive = tmp_path / "source.tgz"
    backup.create_backup(context_name="source", output=str(archive))
    return tmp_path, archive, source_users, original, replacement, selected


def restore(recovery, **kwargs):
    root, archive, *_ = recovery
    return backup.restore_backup(
        context_name=None,
        backup_path=str(archive),
        target_root=str(root / "recovered"),
        provider_context="destination",
        **kwargs,
    )


def test_fresh_database_dry_run_never_replaces_or_writes_receipt(recovery):
    root, _archive, users, original, replacement, _config = recovery
    result = restore(recovery, dry_run=True)
    assert result.target_state == "empty"
    assert replacement.read().users == []
    assert original.read() == users
    assert not (root / "recovered").exists()
    assert not (root / ".recovered.provider-recovery.json").exists()
    assert not result.safety_backup.exists()


def test_fresh_database_recovery_receipt_and_archive_rebinding(recovery):
    root, archive, users, original, replacement, config = recovery
    result = restore(recovery)
    assert original.read() == users
    assert replacement.read() == users
    assert verify_safety_archive(result.safety_backup).users.users == []
    recovered = root / "recovered"
    assert load_config(recovered / "sftpwarden.yaml").provider == config.provider
    entry = local_context("recovered", recovered, ProviderType.MYSQL)
    manifest = verify_safety_archive(archive)
    assert restore_external.protected_provider(entry, manifest) == config.provider
    stage = root / "stage"
    stage.mkdir()
    with tarfile.open(archive) as stream:
        headers = extract_private_archive(stream, stage, private_staging=True)
    original_manifest = validate_backup_manifest(stage, headers)
    rebound = root / "rebound.tgz"
    restore_external.rebind_archive(stage, original_manifest, config.provider, rebound)
    verified = verify_safety_archive(rebound)
    assert verified.config.provider == config.provider
    assert verified.users == users
    for name, metadata in original_manifest.members.items():
        assert {key: verified.members[name][key] for key in ("uid", "gid", "mode")} == {
            key: metadata[key] for key in ("uid", "gid", "mode")
        }


@pytest.mark.parametrize("change", ["phase", "root", "source", "fingerprint", "database", "safety"])
def test_changed_protected_destination_refuses_runtime_recovery(recovery, change):
    root, archive, _users, _original, replacement, _config = recovery
    result = restore(recovery)
    receipt = root / ".recovered.provider-recovery.json"
    record = json.loads(receipt.read_text())
    if change == "database":
        replacement.users = ProviderUsers(schema_version=2, users=[])
    elif change == "safety":
        result.safety_backup.unlink()
    else:
        key = {
            "phase": "phase",
            "root": "target_root",
            "source": "source_provider_destination",
            "fingerprint": "users_fingerprint",
        }[change]
        record[key] = "changed"
        receipt.write_text(json.dumps(record))
    entry = local_context("recovered", root / "recovered", ProviderType.MYSQL)
    with pytest.raises(RuntimeError):
        restore_external.protected_provider(entry, verify_safety_archive(archive))


def test_nonempty_database_is_not_replaced(recovery):
    root, _archive, users, _original, replacement, _config = recovery
    replacement.users = users
    with pytest.raises(RuntimeError, match="empty provider"):
        restore(recovery)
    assert replacement.writes == []
    assert not (root / "recovered").exists()


def test_file_failure_after_database_commit_keeps_recovery_evidence(recovery, monkeypatch):
    root, _archive, users, original, replacement, _config = recovery

    def fail(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(restore_external, "publish_fresh_files", fail)
    with pytest.raises(RuntimeError, match="incomplete"):
        restore(recovery)
    receipt = json.loads((root / ".recovered.provider-recovery.json").read_text())
    assert receipt["phase"] == "provider_restored_files_pending"
    assert verify_safety_archive(Path(receipt["safety_backup"])).users.users == []
    assert replacement.read() == users
    assert original.read() == users
    assert not (root / "recovered").exists()


def test_existing_receipt_blocks_second_provider_replacement(recovery):
    root, _archive, _users, _original, replacement, _config = recovery
    receipt = root / ".recovered.provider-recovery.json"
    receipt.write_text('{"phase":"provider_pending"}')
    with pytest.raises(RuntimeError, match="earlier provider recovery"):
        restore(recovery)
    assert replacement.writes == []
    assert receipt.read_text() == '{"phase":"provider_pending"}'


@pytest.mark.parametrize("mismatch", ["original-destination", "schema", "type"])
def test_external_recovery_rejects_unsafe_destination_before_database_write(recovery, mismatch):
    root, _archive, users, original, replacement, config = recovery
    if mismatch == "original-destination":
        config.provider.table = "sftp_users"
    elif mismatch == "schema":
        config.provider.user_schema = 1
    else:
        config.provider.type = ProviderType.MARIADB
    write_config(root / "connection" / "sftpwarden.yaml", config)
    with pytest.raises(RuntimeError, match="namespace|type and schema"):
        restore(recovery)
    assert replacement.writes == []
    assert original.read() == users
    assert not (root / "recovered").exists()


@pytest.mark.parametrize("problem", ["file-provider", "config-name", "no-local-context"])
def test_external_recovery_requires_explicit_standard_local_selection(
    recovery, monkeypatch, problem
):
    root, archive, _, _, replacement, _ = recovery
    manifest = verify_safety_archive(archive)
    if problem == "file-provider":
        manifest.config.provider.type = ProviderType.YAML
    elif problem == "config-name":
        from dataclasses import replace

        manifest = replace(manifest, config_path="custom.yaml")
    else:
        selected = local_context("destination", root / "connection", ProviderType.MYSQL)
        selected.root = ""
        monkeypatch.setattr(restore_external, "resolve_context", lambda **kw: selected)
    with pytest.raises(RuntimeError):
        restore_external.restore_external_fresh(
            str(root / "new"), "destination", root, manifest, manifest.entries, dry_run=True
        )
    assert not replacement.writes


@pytest.mark.parametrize("problem", ["changed-config", "bad-safety"])
def test_external_destination_is_rechecked_before_first_database_write(
    recovery, monkeypatch, problem
):
    root, _, _, _, replacement, _ = recovery
    if problem == "changed-config":
        original = restore_external.load_config
        reads = []

        def load(path):
            config = original(path)
            reads.append(path)
            if len(reads) >= 2:
                config.project.description = "concurrent edit"
            return config

        monkeypatch.setattr(restore_external, "load_config", load)
    else:
        original = restore_external.verify_safety_archive

        def verify(path):
            from dataclasses import replace

            return replace(original(path), provider_destination="mismatched")

        monkeypatch.setattr(restore_external, "verify_safety_archive", verify)
    with pytest.raises(RuntimeError):
        restore(recovery)
    assert not replacement.writes
    assert not (root / "recovered").exists()


@pytest.mark.parametrize("problem", ["missing-local", "oversize", "invalid-json"])
def test_protected_provider_requires_readable_bounded_receipt(recovery, problem):
    root, archive, *_ = recovery
    restore(recovery)
    entry = local_context("recovered", root / "recovered", ProviderType.MYSQL)
    if problem == "missing-local":
        entry.root = ""
    else:
        receipt = root / ".recovered.provider-recovery.json"
        receipt.write_text("x" * (65537 if problem == "oversize" else 1))
    with pytest.raises(RuntimeError):
        restore_external.protected_provider(entry, verify_safety_archive(archive))
