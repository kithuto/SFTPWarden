from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.cli_commands.core as core_commands
import sftpwarden.services.backup as backup_services
from sftpwarden.cli import app
from sftpwarden.config import ProviderType, default_project_config, dump_config, write_config
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.services.backup import (
    BackupResult,
    backup_entries,
    backup_entries_from_root,
    create_backup,
    create_remote_backup,
    default_backup_path,
    remote_backup_candidates,
    restore_backup,
    restore_remote_backup,
    safe_extract,
    write_backup_archive,
)
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ContextError, RuntimeError


def add_tar_text(archive: tarfile.TarFile, name: str, text: str) -> None:
    """Add a UTF-8 text member to a tar archive."""
    encoded = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(encoded)
    archive.addfile(info, io.BytesIO(encoded))


def tar_bytes(files: dict[str, str], directories: list[str] | None = None) -> bytes:
    """Return a gzipped tar archive as bytes."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for directory in directories or []:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, text in files.items():
            add_tar_text(archive, name, text)
    return stream.getvalue()


def test_backup_archive_entries_and_provider_snapshot(
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Local backups include expected project entries and provider snapshots."""
    root, _entry = local_project_factory()
    for directory in ("host_keys", "state", "data"):
        (root / directory).mkdir()
    config = default_project_config("dev")

    assert backup_entries(root, config, include_data=True) == [
        "data",
        "docker-compose.yml",
        "host_keys",
        "sftpwarden.yaml",
        "state",
        "users.yaml",
    ]
    assert create_backup(context_name="dev", output=None, include_data=False, dry_run=True).entries
    with pytest.raises(RuntimeError, match="Backup file not found"):
        restore_backup(context_name="dev", backup_path=str(root.parent / "missing.tar.gz"))

    assert default_backup_path("dev").name.startswith("sftpwarden-dev-")
    assert "data" in remote_backup_candidates("compose.yml", include_data=True)

    prepared = root.parent / "prepared"
    prepared.mkdir()
    (prepared / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (prepared / "data").mkdir()
    assert backup_entries_from_root(prepared, compose_file="compose.yml", include_data=False) == [
        "compose.yml"
    ]

    output = root.parent / "archive.tar.gz"
    write_backup_archive(
        output_path=output,
        root=root,
        entries=["sftpwarden.yaml", "users.yaml"],
        context_name="dev",
        provider_type="yaml",
        include_data=False,
    )
    with tarfile.open(output, "r:gz") as archive:
        assert "provider/users.json" in archive.getnames()

    missing_provider = root.parent / "missing-provider"
    missing_provider.mkdir()
    write_config(missing_provider / "sftpwarden.yaml", default_project_config("dev"))
    error_archive = root.parent / "error.tar.gz"
    write_backup_archive(
        output_path=error_archive,
        root=missing_provider,
        entries=["sftpwarden.yaml"],
        context_name="dev",
        provider_type="yaml",
        include_data=False,
    )
    with tarfile.open(error_archive, "r:gz") as archive:
        assert "provider/users-error.txt" in archive.getnames()


def test_backup_restore_sqlite_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    test_password_hash: str,
) -> None:
    """Backup and restore preserve SQLite provider state."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    root = tmp_path / "project"
    backup = tmp_path / "backup.tar.gz"

    runner.invoke(app, ["init", "dev", "--root", str(root), "--provider", "sqlite", "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            test_password_hash,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    backup_result = runner.invoke(app, ["backup", "--output", str(backup), "--yes"])

    assert backup_result.exit_code == 0, backup_result.output
    with tarfile.open(backup, "r:gz") as archive:
        names = archive.getnames()
        manifest = json.loads(archive.extractfile("manifest.json").read().decode("utf-8"))  # type: ignore[union-attr]
    assert "users.sqlite" in names
    assert "provider/users.json" in names
    assert manifest["provider"] == "sqlite"

    (root / "users.sqlite").unlink()
    restore_result = runner.invoke(app, ["restore", str(backup), "--yes"])
    users_result = runner.invoke(app, ["users", "--context", "dev", "--json"])

    assert restore_result.exit_code == 0, restore_result.output
    assert users_result.exit_code == 0, users_result.output
    assert json.loads(users_result.output)["users"][0]["username"] == "alice"


def test_backup_includes_external_provider_user_snapshot(
    memory_provider_factory: Callable[[ProviderUsers], object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_factory: Callable[..., SFTPUser],
) -> None:
    """Database-style providers are backed up through provider/users.json."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "mysql-project"
    root.mkdir()
    config = default_project_config("dev", ProviderType.MYSQL, dsn="mysql://db/sftp")
    write_config(root / "sftpwarden.yaml", config)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    save_registry(
        ContextRegistry(
            default="dev",
            contexts={"dev": local_context("dev", root, ProviderType.MYSQL)},
        )
    )
    provider = memory_provider_factory(
        ProviderUsers(users=[user_factory("alice", comment="SQL provider user")])
    )
    monkeypatch.setattr(backup_services, "provider_from_config", lambda *_args: provider)

    backup_path = tmp_path / "mysql-backup.tar.gz"
    result = create_backup(
        context_name="dev",
        output=str(backup_path),
        include_data=False,
        dry_run=False,
    )

    assert "users.yaml" not in result.entries
    with tarfile.open(backup_path, "r:gz") as archive:
        names = archive.getnames()
        snapshot_member = archive.extractfile("provider/users.json")
        assert snapshot_member is not None
        snapshot = json.loads(snapshot_member.read().decode("utf-8"))
    assert "provider/users.json" in names
    assert snapshot["users"][0]["username"] == "alice"
    assert snapshot["users"][0]["comment"] == "SQL provider user"


def test_remote_backup_restore_and_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Remote backup and restore support dry-run and SSH/tar flows."""
    _root, entry = local_project_factory()
    no_remote = entry.model_copy(update={"root": "", "config": ""})
    with pytest.raises(ContextError, match="no local root or remote settings"):
        create_remote_backup(entry=no_remote, output=None, include_data=False, dry_run=False)

    remote = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="/opt/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="archive", contexts={"archive": remote}))

    backup_result = create_backup(
        context_name="archive",
        output=str(tmp_path / "remote-plan.tar.gz"),
        include_data=False,
        dry_run=True,
    )
    assert backup_result.entries[0].startswith("remote:")

    remote_config = default_project_config("archive")
    remote_tar = tar_bytes(
        {
            "sftpwarden.yaml": dump_config(remote_config),
            "docker-compose.yml": "services: {}\n",
            "users.yaml": "users: []\n",
        },
        directories=["state"],
    )

    class Completed:
        returncode = 0
        stdout = remote_tar
        stderr = b""

    monkeypatch.setattr(backup_services.subprocess, "run", lambda *_args, **_kwargs: Completed())
    remote_result = create_remote_backup(
        entry=remote,
        output=str(tmp_path / "remote-backup.tar.gz"),
        include_data=False,
        dry_run=False,
    )
    assert "sftpwarden.yaml" in remote_result.entries

    class Failed:
        returncode = 2
        stdout = b""
        stderr = b"permission denied"

    monkeypatch.setattr(backup_services.subprocess, "run", lambda *_args, **_kwargs: Failed())
    with pytest.raises(RuntimeError, match="Remote backup failed"):
        create_remote_backup(
            entry=remote, output=str(tmp_path / "bad.tar.gz"), include_data=False, dry_run=False
        )

    remote_restore_archive = tmp_path / "remote-restore.tar.gz"
    with tarfile.open(remote_restore_archive, "w:gz") as archive:
        add_tar_text(archive, "manifest.json", json.dumps({"entries": ["sftpwarden.yaml"]}))
        add_tar_text(archive, "sftpwarden.yaml", dump_config(remote_config))
    restored_remote: list[tuple[ContextEntry, list[str]]] = []
    monkeypatch.setattr(
        backup_services,
        "create_backup",
        lambda **_kwargs: BackupResult(path=tmp_path / "safety.tar.gz", entries=[]),
    )
    monkeypatch.setattr(
        backup_services,
        "restore_remote_backup",
        lambda entry, extracted, entries: restored_remote.append((entry, entries)),
    )
    result = restore_backup(context_name="archive", backup_path=str(remote_restore_archive))
    assert result.entries == ["sftpwarden.yaml"]
    assert restored_remote == [(remote, ["sftpwarden.yaml"])]

    dry_archive = tmp_path / "remote-dry-run.tar.gz"
    with tarfile.open(dry_archive, "w:gz") as archive:
        add_tar_text(
            archive,
            "manifest.json",
            json.dumps({"schema_version": 1, "entries": ["sftpwarden.yaml"]}),
        )
    dry_result = restore_backup(
        context_name="archive",
        backup_path=str(dry_archive),
        include_data=False,
        dry_run=True,
    )
    assert dry_result.safety_backup is not None

    extracted = tmp_path / "restore"
    (extracted / "dir").mkdir(parents=True)
    (extracted / "dir" / "file.txt").write_text("data", encoding="utf-8")
    (extracted / "sftpwarden.yaml").write_text("config", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        backup_services,
        "run_checked",
        lambda command, **_kwargs: calls.append(command),
    )
    restore_remote_backup(
        entry=remote,
        extracted=extracted,
        entries=["missing", "dir", "sftpwarden.yaml"],
    )
    assert len(calls) == 2

    broken_remote = remote.model_copy(update={"remote": None})
    with pytest.raises(ContextError, match="missing remote settings"):
        restore_remote_backup(entry=broken_remote, extracted=extracted, entries=[])


def test_restore_external_provider_snapshot_and_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    memory_provider_factory: Callable[[ProviderUsers], object],
    user_factory: Callable[..., SFTPUser],
) -> None:
    """Restore imports provider snapshots and replaces restored directories."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "external"
    root.mkdir()
    config = default_project_config("dev", ProviderType.MYSQL, dsn="mysql://db/sftp")
    write_config(root / "sftpwarden.yaml", config)
    entry = local_context("dev", root, ProviderType.MYSQL)
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))

    archive_path = tmp_path / "external.tar.gz"
    manifest = {
        "schema_version": 1,
        "entries": ["sftpwarden.yaml", "data/file.txt", "missing.txt"],
    }
    with tarfile.open(archive_path, "w:gz") as archive:
        add_tar_text(archive, "manifest.json", json.dumps(manifest))
        add_tar_text(archive, "sftpwarden.yaml", dump_config(config))
        add_tar_text(
            archive,
            "provider/users.json",
            json.dumps(
                {"users": [user_factory("alice").model_dump(mode="json", exclude_none=True)]}
            ),
        )

    provider = memory_provider_factory(ProviderUsers(users=[]))
    monkeypatch.setattr(backup_services, "provider_from_config", lambda *_args: provider)
    monkeypatch.setattr(
        backup_services,
        "create_backup",
        lambda **_kwargs: BackupResult(path=tmp_path / "safety.tar.gz", entries=[]),
    )
    result = restore_backup(
        context_name="dev",
        backup_path=str(archive_path),
        include_data=False,
        dry_run=False,
    )
    assert result.entries == ["sftpwarden.yaml", "missing.txt"]
    assert provider.writes[0].users[0].username == "alice"  # type: ignore

    directory_archive = tmp_path / "directory.tar.gz"
    with tarfile.open(directory_archive, "w:gz") as archive:
        add_tar_text(archive, "manifest.json", json.dumps({"entries": ["host_keys"]}))
        directory = tarfile.TarInfo("host_keys")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        add_tar_text(archive, "host_keys/ssh_host_ed25519_key", "new-key")
    (root / "host_keys").mkdir()
    (root / "host_keys" / "old").write_text("old", encoding="utf-8")
    result = restore_backup(
        context_name="dev",
        backup_path=str(directory_archive),
        include_data=False,
        dry_run=False,
    )
    assert result.entries == ["host_keys"]
    assert not (root / "host_keys" / "old").exists()
    assert (root / "host_keys" / "ssh_host_ed25519_key").read_text(encoding="utf-8") == "new-key"


def test_safe_extract_rejects_unsafe_members(tmp_path: Path) -> None:
    """Tar extraction rejects traversal, non-files and unreadable members."""
    unsafe = tmp_path / "unsafe.tar.gz"
    with tarfile.open(unsafe, "w:gz") as archive:
        add_tar_text(archive, "../bad", "bad")
    with tarfile.open(unsafe, "r:gz") as archive, pytest.raises(RuntimeError, match="unsafe"):
        safe_extract(archive, tmp_path / "out")

    link_archive = tmp_path / "link.tar.gz"
    with tarfile.open(link_archive, "w:gz") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        archive.addfile(info)
    with (
        tarfile.open(link_archive, "r:gz") as archive,
        pytest.raises(RuntimeError, match="unsupported"),
    ):
        safe_extract(archive, tmp_path / "out")

    class FakeMember:
        name = "file.txt"

        def isdir(self) -> bool:
            return False

        def isfile(self) -> bool:
            return True

    class FakeArchive:
        def getmembers(self) -> list[FakeMember]:
            return [FakeMember()]

        def extractfile(self, _member: FakeMember) -> None:
            return None

    with pytest.raises(RuntimeError, match="Cannot read"):
        safe_extract(FakeArchive(), tmp_path / "out")  # type: ignore[arg-type]


def test_backup_restore_cli_confirmations_and_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Backup and restore CLI commands handle confirmations, JSON and service errors."""
    runner = CliRunner()
    backup_result = BackupResult(path=tmp_path / "backup.tar.gz", entries=["sftpwarden.yaml"])
    monkeypatch.setattr(core_commands, "create_backup", lambda **_kwargs: backup_result)
    monkeypatch.setattr(core_commands.Confirm, "ask", lambda *_args, **_kwargs: False)
    assert runner.invoke(app, ["backup", "--include-data"]).exit_code == 1
    assert runner.invoke(app, ["backup", "--dry-run"]).exit_code == 0
    backup_json = runner.invoke(app, ["backup", "--json", "--yes"])
    assert json.loads(backup_json.output)["entries"] == ["sftpwarden.yaml"]
    monkeypatch.setattr(
        core_commands,
        "create_backup",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("backup failed")),
    )
    assert runner.invoke(app, ["backup", "--yes"]).exit_code == 1

    restore_result = BackupResult(
        path=tmp_path / "backup.tar.gz",
        entries=["sftpwarden.yaml"],
        safety_backup=tmp_path / "safety.tar.gz",
    )
    monkeypatch.setattr(core_commands, "restore_backup", lambda **_kwargs: restore_result)
    monkeypatch.setattr(core_commands.Confirm, "ask", lambda *_args, **_kwargs: False)
    assert runner.invoke(app, ["restore", "backup.tar.gz"]).exit_code == 1
    assert runner.invoke(app, ["restore", "backup.tar.gz", "--include-data"]).exit_code == 1

    answers = iter([True, False])
    monkeypatch.setattr(core_commands.Confirm, "ask", lambda *_args, **_kwargs: next(answers))
    assert runner.invoke(app, ["restore", "backup.tar.gz", "--include-data"]).exit_code == 1

    monkeypatch.setattr(core_commands.Confirm, "ask", lambda *_args, **_kwargs: False)
    assert runner.invoke(app, ["restore", "backup.tar.gz", "--dry-run"]).exit_code == 0
    restore_json = runner.invoke(app, ["restore", "backup.tar.gz", "--json", "--yes"])
    assert json.loads(restore_json.output)["safety_backup"].endswith("safety.tar.gz")
    monkeypatch.setattr(
        core_commands,
        "restore_backup",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("restore failed")),
    )
    assert runner.invoke(app, ["restore", "backup.tar.gz", "--yes"]).exit_code == 1
