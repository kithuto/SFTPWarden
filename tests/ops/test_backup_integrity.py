import io
import json
import os
import sqlite3
import tarfile
from contextlib import closing

import pytest

from sftpwarden.config import ProviderType, load_config, provider_local_path, write_config
from sftpwarden.providers import provider_from_config
from sftpwarden.services.backup import (
    create_backup,
    restore_backup,
    safe_extract,
    write_backup_archive,
)
from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError, RuntimeError


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate-json",
        "not-object",
        "version",
        "destination",
        "no-headers",
        "entries",
        "duplicates",
        "overlap",
        "config-type",
        "config-reserved",
        "missing-member",
        "snapshot",
        "schema",
        "compose-reserved",
        "provider-omitted",
        "provider-mismatch",
        "unmanaged",
    ],
)
def test_manifest_validation_rejects_incomplete_or_ambiguous_recovery(
    local_project_factory, tmp_path, damage
):
    root, entry = local_project_factory()
    packed = tmp_path / "point.tgz"
    create_backup(context_name=entry.name, output=str(packed))
    staging = tmp_path / "staging"
    with tarfile.open(packed) as archive:
        safe_extract(archive, staging)
    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 1
    config = load_config(staging / "sftpwarden.yaml")
    if damage == "duplicate-json":
        manifest_path.write_text('{"schema_version":1,"schema_version":2}')
    else:
        if damage == "not-object":
            manifest = []
        elif damage == "version":
            manifest["schema_version"] = 99
        elif damage == "destination":
            manifest["provider_destination"] = "invalid"
        elif damage == "no-headers":
            manifest["schema_version"] = 2
        elif damage == "entries":
            manifest["entries"] = []
        elif damage == "duplicates":
            manifest["entries"].append(manifest["entries"][0])
        elif damage == "overlap":
            manifest["entries"] += ["data", "data/alice"]
        elif damage == "config-type":
            manifest["config_path"] = 42
        elif damage == "config-reserved":
            manifest["config_path"] = "state/config.yaml"
        elif damage == "missing-member":
            manifest["entries"].append("absent.txt")
        elif damage == "snapshot":
            (staging / "provider/users.json").write_text("invalid")
        elif damage == "schema":
            config.provider.user_schema = 1
            write_config(staging / "sftpwarden.yaml", config)
        elif damage == "compose-reserved":
            config.docker.compose_file = "state/runtime.yaml"
            write_config(staging / "sftpwarden.yaml", config)
        elif damage == "provider-omitted":
            manifest["entries"].remove("users.yaml")
        elif damage == "provider-mismatch":
            provider_from_config(staging, config).write(
                ProviderUsers(
                    schema_version=config.provider.user_schema,
                    users=[SFTPUser(username="unexpected", disabled=True)],
                )
            )
        elif damage == "unmanaged":
            (staging / "unmanaged").write_text("must not publish")
            manifest["entries"].append("unmanaged")
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        validate_backup_manifest(staging)


def test_missing_provider_cannot_publish_or_replace_a_successful_archive(local_project_factory):
    root, entry = local_project_factory()
    (root / "users.yaml").unlink()
    output = root.parent / "backup.tar.gz"
    output.write_bytes(b"previous recovery point")
    with pytest.raises(ProviderError):
        create_backup(context_name=entry.name, output=str(output))
    assert output.read_bytes() == b"previous recovery point"


def test_failed_archive_write_keeps_previous_output_and_removes_staging(
    local_project_factory,
    monkeypatch,
):
    root, entry = local_project_factory()
    output = root.parent / "backup.tar.gz"
    output.write_bytes(b"previous recovery point")

    def fail_add(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(tarfile.TarFile, "addfile", fail_add)
    with pytest.raises(OSError, match="simulated disk full"):
        create_backup(context_name=entry.name, output=str(output))
    assert output.read_bytes() == b"previous recovery point"
    assert not list(output.parent.glob(f".{output.name}.*"))


def test_sqlite_archive_contains_committed_wal_data(local_project_factory, tmp_path):
    root, entry = local_project_factory(provider=ProviderType.SQLITE)
    config = load_config(entry.config)
    path = provider_local_path(root, config)
    provider = provider_from_config(root, config)
    provider.write(ProviderUsers(users=[SFTPUser(username="alice", disabled=True)]))
    output = root.parent / "backup.tar.gz"
    with closing(sqlite3.connect(path)) as held, held:
        held.execute("PRAGMA journal_mode=WAL")
        held.execute("PRAGMA wal_autocheckpoint=0")
        held.execute("INSERT INTO sftp_users (username, disabled) VALUES ('bob', 1)")
        held.commit()
        assert path.with_name(f"{path.name}-wal").stat().st_size > 0
        create_backup(context_name=entry.name, output=str(output))
        staging = tmp_path / "extracted"
        with tarfile.open(output) as archive:
            safe_extract(archive, staging)
        restored = provider_from_config(staging, config).read()
        assert {user.username for user in restored.users} == {"alice", "bob"}


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape",
        "safe/../../escape",
        "C:/escape",
        "safe\\escape",
        "users.yaml.",
        "NUL",
        "data/COM1.txt",
        "data/alternate:stream",
        "data/file ",
    ],
)
def test_archive_preflight_rejects_unsafe_paths_before_extracting_any_file(tmp_path, unsafe):
    path = tmp_path / "unsafe.tar"
    with tarfile.open(path, "w") as archive:
        for name in ["safe.txt", unsafe]:
            member = tarfile.TarInfo(name)
            member.size = 4
            archive.addfile(member, io.BytesIO(b"data"))
    output = tmp_path / "output"
    with tarfile.open(path) as archive, pytest.raises(RuntimeError, match="unsafe"):
        safe_extract(archive, output)
    assert not output.exists() or not list(output.iterdir())


def test_archive_preflight_rejects_duplicate_members(tmp_path):
    path = tmp_path / "duplicate.tar"
    with tarfile.open(path, "w") as archive:
        for content in [b"original", b"replacement"]:
            member = tarfile.TarInfo("config.yaml")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    with tarfile.open(path) as archive, pytest.raises(RuntimeError, match="duplicate"):
        safe_extract(archive, tmp_path / "output")


@pytest.mark.parametrize(
    "names",
    [
        ["users.yaml", "USERS.yaml"],
        ["data/café", "data/cafe\u0301"],
        ["file", "file/nested"],
    ],
)
def test_archive_rejects_portable_path_collisions_before_extraction(tmp_path, names):
    path = tmp_path / "collision.tar"
    with tarfile.open(path, "w") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            archive.addfile(info, io.BytesIO())
    destination = tmp_path / "output"
    with tarfile.open(path) as archive, pytest.raises(RuntimeError):
        safe_extract(archive, destination)
    assert not destination.exists()


def test_backup_requires_explicit_physical_provider_entry(local_project_factory):
    root, entry = local_project_factory()
    output = root.parent / "incomplete.tar.gz"
    with pytest.raises(RuntimeError, match="omit required"):
        write_backup_archive(
            output_path=output,
            root=root,
            entries=["sftpwarden.yaml"],
            context_name=entry.name,
            provider_type="yaml",
            include_data=False,
        )
    assert not output.exists()


def test_backup_requires_the_configured_provider_spelling(local_project_factory):
    root, entry = local_project_factory()
    (root / "USERS.yaml").write_bytes((root / "users.yaml").read_bytes())
    output = root.parent / "mismatched.tar.gz"
    with pytest.raises(RuntimeError, match="omit required"):
        write_backup_archive(
            output_path=output,
            root=root,
            entries=["sftpwarden.yaml", "USERS.yaml"],
            context_name=entry.name,
            provider_type="yaml",
            include_data=False,
        )
    assert not output.exists()


@pytest.mark.parametrize("damage", ["payload", "mode", "owner", "missing", "extra"])
def test_backup_detects_a_modified_data_member_before_touching_target(
    local_project_factory,
    monkeypatch,
    damage,
):
    import sftpwarden.services.backup as backups

    root, entry = local_project_factory()
    (root / "data").mkdir()
    payload = root / "data" / "payload.txt"
    payload.write_text("original")
    output = root.parent / "original.tar.gz"
    modified = root.parent / "modified.tar.gz"
    write_backup_archive(
        output_path=output,
        root=root,
        entries=["sftpwarden.yaml", "users.yaml", "data"],
        context_name=entry.name,
        provider_type="yaml",
        include_data=True,
    )
    with tarfile.open(output) as source, tarfile.open(modified, "w:gz") as destination:
        for member in source:
            if member.name == "data/payload.txt" and damage == "payload":
                content = b"tampered"
                member.size = len(content)
                destination.addfile(member, io.BytesIO(content))
            else:
                if member.name == "data/payload.txt":
                    if damage == "missing":
                        continue
                    if damage == "mode":
                        member.mode ^= 0o020
                    if damage == "owner":
                        member.uid += 1
                destination.addfile(member, source.extractfile(member) if member.isfile() else None)
        if damage == "extra":
            destination.addfile(tarfile.TarInfo("data/extra"), io.BytesIO())
    called = []
    monkeypatch.setattr(backups, "create_backup", lambda **kwargs: called.append(kwargs))
    with pytest.raises(RuntimeError, match="integrity"):
        restore_backup(context_name=entry.name, backup_path=str(modified), include_data=True)
    assert not called
    assert payload.read_text() == "original"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_backup_is_private_even_with_a_permissive_umask(local_project_factory):
    root, entry = local_project_factory()
    output = root.parent / "private.tar.gz"
    previous = os.umask(0)
    try:
        create_backup(context_name=entry.name, output=str(output))
    finally:
        os.umask(previous)
    assert output.stat().st_mode & 0o777 == 0o600


def test_archive_expansion_limits_are_checked_before_any_write(tmp_path, monkeypatch):
    import sftpwarden.utils.archives as extraction

    monkeypatch.setattr(extraction, "MAX_ARCHIVE_BYTES", 3)
    path = tmp_path / "large.tar"
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo("file")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"data"))
    destination = tmp_path / "output"
    with tarfile.open(path) as archive, pytest.raises(RuntimeError, match="limits"):
        safe_extract(archive, destination)
    assert not destination.exists()


def test_backup_cannot_publish_an_archive_over_its_restore_limit(
    local_project_factory,
    monkeypatch,
):
    import sftpwarden.services.backup as backups

    root, entry = local_project_factory()
    output = root.parent / "over-limit.tar.gz"
    output.write_bytes(b"prior recovery point")
    monkeypatch.setattr(backups, "MAX_ARCHIVE_MEMBERS", 2)
    with pytest.raises(RuntimeError, match="limits"):
        create_backup(context_name=entry.name, output=str(output))
    assert output.read_bytes() == b"prior recovery point"


def test_backup_rejects_source_links_before_publication(local_project_factory):
    root, entry = local_project_factory()
    (root / "data").mkdir()
    (root / "data" / "linked").symlink_to(root / "users.yaml")
    output = root.parent / "backup.tar.gz"
    with pytest.raises(RuntimeError, match="unsupported"):
        write_backup_archive(
            output_path=output,
            root=root,
            entries=["sftpwarden.yaml", "users.yaml", "data"],
            context_name=entry.name,
            provider_type="yaml",
            include_data=True,
        )
    assert not output.exists()


@pytest.mark.parametrize("entry", ["../outside", ".git/config", "missing.txt"])
def test_restore_rejects_invalid_manifest_before_safety_backup(
    local_project_factory,
    monkeypatch,
    entry,
):
    import sftpwarden.services.backup as backups

    root, context = local_project_factory()
    archive_path = root.parent / "invalid.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = json.dumps({"schema_version": 1, "entries": [entry]}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        if entry == ".git/config":
            info = tarfile.TarInfo(entry)
            info.size = 4
            archive.addfile(info, io.BytesIO(b"evil"))
    called = []
    monkeypatch.setattr(backups, "create_backup", lambda **kwargs: called.append(kwargs))
    with pytest.raises(RuntimeError):
        restore_backup(context_name=context.name, backup_path=str(archive_path))
    assert not called
    assert not (root / ".git").exists()
