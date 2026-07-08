from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest
import yaml

from .conftest import (
    FAKE_KEY_1,
    FAKE_KEY_2,
    FAKE_KEY_3,
    TEST_HASH,
    ReleaseCli,
    assert_failed,
    assert_ok,
)


@pytest.mark.release_validation
def test_local_yaml_project_full_user_and_key_lifecycle(cli: ReleaseCli, tmp_path: Path) -> None:
    """Exercise the day-to-day local YAML workflow as a real operator would."""
    root = tmp_path / "yaml-project"
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    (key_dir / "laptop.pub").write_text(FAKE_KEY_3, encoding="utf-8")

    assert_ok(cli.run("init", "dev", "--root", root, "--yes"))
    assert_ok(cli.run("validate", "--config", root / "sftpwarden.yaml", "--json"))
    assert_ok(cli.run("info", "--json"))
    assert_ok(cli.run("context", "current"))
    assert_ok(cli.run("context", "ls", "--json"))
    assert_ok(cli.run("config", "server.port", "2244"))
    assert_ok(cli.run("config", "sync.interval_seconds", "15"))
    assert_ok(cli.run("config", "healthcheck.interval_seconds", "5"))
    assert_ok(cli.run("config", "server.port"))

    assert_ok(
        cli.run(
            "user",
            "create",
            "alice",
            "--password-hash",
            TEST_HASH,
            "--comment",
            "Finance dropbox",
            "--upload-dir",
            "inbound",
            "--uid",
            "12001",
            "--gid",
            "12001",
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run(
            "user",
            "update",
            "alice",
            "--comment",
            "Finance archive",
            "--upload-dir",
            "uploads",
            "--no-refresh",
        )
    )
    assert_ok(cli.run("user", "disable", "alice", "--no-refresh"))
    assert_ok(cli.run("user", "enable", "alice", "--no-refresh"))
    assert_ok(
        cli.run(
            "user",
            "key",
            "add",
            "alice",
            "prod-ci",
            "--public-key",
            FAKE_KEY_1,
            "--comment",
            "CI deploy key",
            "--no-refresh",
        )
    )
    assert_ok(cli.run("user", "key", "list", "alice"))
    key_show = cli.run("user", "key", "show", "alice", "prod-ci")
    assert_ok(key_show)
    assert json.loads(key_show.stdout)["name"] == "prod-ci"
    assert_ok(cli.run("user", "key", "disable", "alice", "prod-ci", "--yes", "--no-refresh"))
    assert_ok(cli.run("user", "key", "enable", "alice", "prod-ci", "--yes", "--no-refresh"))
    assert_ok(
        cli.run(
            "user",
            "key",
            "rename",
            "alice",
            "prod-ci",
            "prod-renamed",
            "--yes",
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run(
            "user",
            "key",
            "rotate",
            "alice",
            "prod-renamed",
            "--public-key",
            FAKE_KEY_2,
            "--yes",
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run(
            "user",
            "key",
            "expire",
            "alice",
            "prod-renamed",
            "--at",
            "2027-01-01",
            "--yes",
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run("user", "key", "import", "alice", "--from-dir", key_dir, "--yes", "--no-refresh")
    )
    assert_ok(cli.run("user", "key", "remove", "alice", "laptop", "--yes", "--no-refresh"))

    users = cli.run("users", "--json")
    user_show = cli.run("user", "show", "alice")
    plan = cli.run("plan", "--json")

    assert_ok(users)
    assert_ok(user_show)
    assert_ok(plan)
    assert json.loads(users.stdout)["users"][0]["username"] == "alice"
    assert json.loads(user_show.stdout)["comment"] == "Finance archive"
    assert json.loads(plan.stdout)["actions"][0]["username"] == "alice"

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))
    assert provider["schema_version"] == 2
    assert provider["users"][0]["keys"][0]["name"] == "prod-renamed"
    assert "correct horse" not in (root / "users.yaml").read_text(encoding="utf-8")


@pytest.mark.release_validation
@pytest.mark.parametrize(
    ("provider", "filename"),
    [("yaml", "users.yaml"), ("csv", "users.csv"), ("sqlite", "users.sqlite")],
)
def test_file_provider_init_validate_export_import_and_backup(
    cli: ReleaseCli,
    tmp_path: Path,
    provider: str,
    filename: str,
) -> None:
    """File-backed providers should support real operator import/export and backups."""
    root = tmp_path / f"{provider}-project"
    export_path = tmp_path / f"{provider}-users.json"
    backup_path = tmp_path / f"{provider}-backup.tar.gz"

    assert_ok(cli.run("init", provider, "--provider", provider, "--root", root, "--yes"))
    assert (root / filename).exists()
    assert_ok(
        cli.run(
            "user",
            "create",
            "alice",
            "--password-hash",
            TEST_HASH,
            "--context",
            provider,
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run(
            "provider",
            "export",
            "--context",
            provider,
            "--format",
            "json",
            "--output",
            export_path,
        )
    )
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["users"][0]["username"] == "alice"

    assert_ok(cli.run("user", "remove", "alice", "--context", provider, "--yes", "--no-refresh"))
    imported = cli.run(
        "provider",
        "import",
        "--context",
        provider,
        "--input",
        export_path,
        "--replace",
        "--json",
        "--no-refresh",
    )
    assert_ok(imported)
    assert json.loads(imported.stdout)["changed"]

    dry_backup = cli.run(
        "backup", "--context", provider, "--output", backup_path, "--dry-run", "--json"
    )
    assert_ok(dry_backup)
    assert not backup_path.exists()
    assert filename in json.loads(dry_backup.stdout)["entries"]

    assert_ok(cli.run("backup", "--context", provider, "--output", backup_path, "--yes"))
    assert backup_path.exists()
    assert_ok(cli.run("restore", backup_path, "--context", provider, "--dry-run", "--json"))


@pytest.mark.release_validation
def test_provider_copy_schema_migration_and_remote_sync_guidance(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """Provider transfer, schema migration, and remote local-sync dry-runs should compose."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    simple = tmp_path / "simple"
    watcher_mode = "windows-task" if platform.system() == "Windows" else "systemd"

    assert_ok(cli.run("init", "source", "--root", source, "--yes"))
    assert_ok(cli.run("init", "dest", "--provider", "csv", "--root", dest, "--yes"))
    assert_ok(cli.run("init", "simple", "--root", simple, "--user-schema", "1", "--yes"))
    assert_ok(
        cli.run(
            "user",
            "create",
            "alice",
            "--password-hash",
            TEST_HASH,
            "--context",
            "source",
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run(
            "user",
            "create",
            "legacy",
            "--public-key",
            FAKE_KEY_1,
            "--context",
            "simple",
            "--no-refresh",
        )
    )

    copied = cli.run(
        "provider",
        "copy",
        "--from-context",
        "source",
        "--to-context",
        "dest",
        "--replace",
        "--json",
        "--no-refresh",
    )
    assert_ok(copied)
    assert json.loads(copied.stdout)["destination_count"] == 1

    schema = cli.run("provider", "schema", "show", "--context", "simple", "--json")
    migration = cli.run(
        "provider",
        "keys",
        "migrate",
        "--context",
        "simple",
        "--dry-run",
        "--json",
    )
    applied = cli.run("provider", "schema", "migrate", "--to", "2", "--context", "simple", "--yes")
    assert_ok(schema)
    assert_ok(migration)
    assert_ok(applied)
    assert json.loads(schema.stdout)["provider_user_schema"] == 1
    assert json.loads(migration.stdout)["changed"]

    assert_ok(
        cli.run(
            "context",
            "add",
            "remote-dev",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            source,
            "--provider",
            "yaml",
            "--watcher",
            watcher_mode,
            "--critical",
            "--skip-checks",
            "--yes",
        )
    )
    sync = cli.run("sync", "--dry-run", "--json")
    watcher_status = cli.run("watcher", "status", "--json")
    watcher_uninstall = cli.run("watcher", "uninstall", "--yes")
    watcher_status_after = cli.run("watcher", "status", "--json")

    assert_ok(sync)
    assert_ok(watcher_status)
    assert_ok(watcher_uninstall)
    assert_ok(watcher_status_after)
    sync_data = json.loads(sync.stdout)
    watcher_data = json.loads(watcher_status.stdout)
    watcher_after_data = json.loads(watcher_status_after.stdout)
    assert sync_data["targets"][0]["context"] == "remote-dev"
    assert watcher_data["installed"] is True
    assert watcher_data["mode"] == watcher_mode
    assert watcher_data["targets"][0]["context"] == "remote-dev"
    assert watcher_after_data["installed"] is False


@pytest.mark.release_validation
def test_backup_restore_with_data_uses_explicit_confirmation(
    cli: ReleaseCli, tmp_path: Path
) -> None:
    """Backup/restore should preserve data only when explicitly requested."""
    root = tmp_path / "backup-project"
    backup_path = tmp_path / "full-backup.tar.gz"

    assert_ok(cli.run("init", "backup", "--root", root, "--yes"))
    assert_ok(
        cli.run(
            "user",
            "create",
            "alice",
            "--password-hash",
            TEST_HASH,
            "--no-refresh",
        )
    )
    payload = root / "data" / "alice" / "upload" / "payload.txt"
    payload.parent.mkdir(parents=True)
    payload.write_text("release payload", encoding="utf-8")

    refused = cli.run("backup", "--include-data", "--output", backup_path, input_text="n\n")
    assert refused.returncode != 0
    assert not backup_path.exists()
    assert_ok(cli.run("backup", "--include-data", "--output", backup_path, "--yes"))

    payload.write_text("changed", encoding="utf-8")
    assert_ok(cli.run("restore", backup_path, "--include-data", "--yes"))
    assert payload.read_text(encoding="utf-8") == "release payload"


@pytest.mark.release_validation
def test_common_error_paths_are_user_friendly(cli: ReleaseCli, tmp_path: Path) -> None:
    """Common invalid user actions should be controlled and actionable."""
    root = tmp_path / "errors"
    empty_keys = tmp_path / "empty-keys"
    empty_keys.mkdir()

    assert_failed(cli.run("context", "ls"), "No SFTPWarden context has been initialized.")
    assert_failed(
        cli.run("validate", "--config", tmp_path / "missing.yaml"),
        "Config file not found",
        "Run `sftpwarden init <name>`",
    )

    assert_ok(cli.run("init", "errors", "--root", root, "--yes"))
    assert_failed(
        cli.run(
            "user",
            "create",
            "alice",
            "--password",
            "correct horse battery staple",
            "--password-hash",
            TEST_HASH,
            "--no-refresh",
        ),
        "Use either --password or --password-hash",
    )
    assert_failed(cli.run("user", "show", "missing"), "Unknown user: missing")
    assert_failed(
        cli.run("provider", "import", "--input", root / "users.yaml", "--json"),
        "Use exactly one of --merge or --replace",
    )
    assert_failed(
        cli.run("user", "key", "import", "alice", "--from-dir", empty_keys),
        "No .pub files found",
    )

    config = root / "sftpwarden.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["kubernetes"]["replicas"] = 2
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert_failed(
        cli.run("validate", "--config", config),
        "Kubernetes replicas > 1 are not supported",
        "Set kubernetes.replicas to 1.",
    )
