from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

import sftpwarden.cli_commands.init as init_commands
import sftpwarden.services.cli_workflows as cli_workflows
from sftpwarden.cli import app
from sftpwarden.config import load_config, write_config
from sftpwarden.contexts import load_registry


def test_init_named_context_creates_project_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()

    result = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    assert result.exit_code == 0, result.output
    config = load_config(root / "sftpwarden.yaml")
    assert config.project.name == "dev"
    assert (root / "users.yaml").exists()
    assert (root / "docker-compose.yml").exists()
    assert ((root / "sftpwarden.yaml").stat().st_mode & 0o777) == 0o600
    assert ((root / "users.yaml").stat().st_mode & 0o777) == 0o600


def test_init_without_root_uses_current_directory_and_sets_active_context(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    root.mkdir()
    monkeypatch.chdir(root)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "dev", "--yes"])
    current = runner.invoke(app, ["context", "current"])

    assert result.exit_code == 0, result.output
    assert (root / "sftpwarden.yaml").exists()
    assert (root / "users.yaml").exists()
    assert (root / "docker-compose.yml").exists()
    assert load_config(root / "sftpwarden.yaml").project.name == "dev"
    assert load_registry().default == "dev"
    assert current.exit_code == 0, current.output
    assert current.output.strip() == "dev"


def test_deploy_uses_active_local_context_and_builds_compose(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    root.mkdir()
    monkeypatch.chdir(root)
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--yes"])

    result = runner.invoke(app, ["deploy", "--dry-run"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "docker compose -f docker-compose.yml up -d --build" in output


def test_plan_explains_detected_changes_will_be_applied(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    result = runner.invoke(app, ["plan"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "User/provider changes detected" in output
    assert "These actions will be applied by `sftpwarden refresh`" in output


def test_plan_explains_detected_config_changes_require_deploy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(app, ["config", "server.port", "2200"])

    result = runner.invoke(app, ["plan"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "Configuration/deploy changes detected" in output
    assert "These changes will be applied by `sftpwarden deploy`" in output
    assert "`sftpwarden refresh` only applies user/provider changes" in output
    assert "docker-compose.yml differs from current configuration" in output


def test_plan_json_reports_detected_config_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(app, ["config", "server.port", "2200"])

    result = runner.invoke(app, ["plan", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["deploy_config_changed"] is True
    assert data["deploy_config_reasons"] == [
        "docker-compose.yml differs from current configuration"
    ]


def test_config_command_reads_and_updates_project_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    read_result = runner.invoke(app, ["config", "server.port"])
    null_result = runner.invoke(app, ["config", "provider.dsn"])
    update_result = runner.invoke(app, ["config", "server.port", "2200"])

    config = load_config(root / "sftpwarden.yaml")
    assert read_result.exit_code == 0, read_result.output
    assert read_result.output.strip() == "2222"
    assert null_result.exit_code == 0, null_result.output
    assert null_result.output.strip() == "null"
    assert update_result.exit_code == 0, update_result.output
    assert config.server.port == 2200


def test_config_project_name_renames_active_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(app, ["config", "project.name", "dev2"])
    current = runner.invoke(app, ["context", "current"])
    registry = load_registry()

    assert result.exit_code == 0, result.output
    assert current.exit_code == 0, current.output
    assert current.output.strip() == "dev2"
    assert registry.default == "dev2"
    assert "dev" not in registry.contexts
    assert load_config(root / "sftpwarden.yaml").project.name == "dev2"


def test_context_name_updates_registry_and_project_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(app, ["context", "name", "dev2"])
    registry = load_registry()

    assert result.exit_code == 0, result.output
    assert registry.default == "dev2"
    assert "dev2" in registry.contexts
    assert "dev" not in registry.contexts
    assert load_config(root / "sftpwarden.yaml").project.name == "dev2"


def test_context_root_migrates_project_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    old_root = tmp_path / "old-project"
    new_root = tmp_path / "new-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(old_root), "--yes"])

    result = runner.invoke(
        app,
        ["context", "root", str(new_root), "--yes", "--delete-old-root"],
    )
    registry = load_registry()
    entry = registry.contexts["dev"]

    assert result.exit_code == 0, result.output
    assert not old_root.exists()
    assert (new_root / "sftpwarden.yaml").exists()
    assert (new_root / "users.yaml").exists()
    assert entry.root == str(new_root)
    assert entry.config == str(new_root / "sftpwarden.yaml")


def test_context_type_converts_between_local_and_remote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    remote_result = runner.invoke(
        app,
        [
            "context",
            "type",
            "remote",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--yes",
        ],
    )
    local_result = runner.invoke(app, ["context", "type", "local", "--yes"])
    entry = load_registry().contexts["dev"]

    assert remote_result.exit_code == 0, remote_result.output
    assert local_result.exit_code == 0, local_result.output
    assert entry.type == "local"
    assert entry.remote is None
    assert entry.root == str(root)


def test_context_type_warns_when_installed_watcher_has_no_local_sync_targets(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "init",
            "prod",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            str(root),
            "--critical",
            "--skip-checks",
            "--yes",
            "--watcher",
            "systemd",
        ],
    )

    result = runner.invoke(app, ["context", "type", "local", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Watcher is installed but there are no remote local-sync contexts left" in result.output
    assert "sftpwarden watcher uninstall" in result.output


def test_manual_project_name_change_reconciles_registered_context(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    config = load_config(root / "sftpwarden.yaml")
    config.project.name = "manual"
    write_config(root / "sftpwarden.yaml", config)

    current_before_deploy = runner.invoke(app, ["context", "current"])
    deploy = runner.invoke(app, ["deploy", "--dry-run"])
    current_after_deploy = runner.invoke(app, ["context", "current"])
    registry = load_registry()

    assert current_before_deploy.exit_code == 0, current_before_deploy.output
    assert current_before_deploy.output.strip() == "dev"
    assert deploy.exit_code == 0, deploy.output
    assert current_after_deploy.exit_code == 0, current_after_deploy.output
    assert current_after_deploy.output.strip() == "manual"
    assert registry.default == "manual"
    assert "manual" in registry.contexts
    assert "dev" not in registry.contexts


def test_init_sql_provider_can_create_missing_table(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "sql-project"
    created: list[bool] = []

    class FakeSQLProvider:
        def table_exists(self) -> bool:
            return False

        def create_table(self) -> None:
            created.append(True)

    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: FakeSQLProvider())
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "sql-dev",
            "--root",
            str(root),
            "--provider",
            "mysql",
            "--dsn",
            "mysql://user:pass@localhost/sftp",
            "--create-table",
            "--yes",
        ],
    )

    config = load_config(root / "sftpwarden.yaml")

    assert result.exit_code == 0, result.output
    assert created == [True]
    assert config.provider.type == "mysql"
    assert config.provider.dsn == "mysql://user:pass@localhost/sftp"
    assert not (root / "users.yaml").exists()


def test_init_sql_provider_can_abort_when_table_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "sql-project"

    class FakeSQLProvider:
        def table_exists(self) -> bool:
            return False

        def create_table(self) -> None:
            raise AssertionError("table should not be created")

    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: FakeSQLProvider())
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "sql-dev",
            "--root",
            str(root),
            "--provider",
            "postgresql",
            "--dsn",
            "postgresql://user:pass@localhost/sftp",
            "--no-create-table",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "SQL users table does not exist" in result.output
    assert not (root / "sftpwarden.yaml").exists()


def test_validate_json_reports_config_and_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(app, ["validate", "--config", str(root / "sftpwarden.yaml"), "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["valid"] is True
    assert data["project"] == "dev"
    assert data["provider"] == "yaml"
    assert data["provider_path"] == str(root / "users.yaml")


def test_doctor_json_reports_checks() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert {check["name"] for check in data["checks"]} == {"docker", "ssh", "rsync"}


def test_user_add_hashes_plaintext_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "bob",
            "--password",
            "correct horse battery staple",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))
    bob = provider["users"][0]

    assert result.exit_code == 0, result.output
    assert bob["username"] == "bob"
    assert bob["password_hash"].startswith("$6$")
    assert "correct horse battery staple" not in (root / "users.yaml").read_text(encoding="utf-8")


def test_user_add_accepts_existing_password_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    password_hash = "$6$rounds=500000$saltstring$hashvalue"

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "carol",
            "--password-hash",
            password_hash,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"][0]["password_hash"] == password_hash


def test_user_add_stores_comment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "erin",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--comment",
            "Finance dropbox",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"][0]["comment"] == "Finance dropbox"


def test_user_show_accepts_config_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "heidi",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    result = runner.invoke(
        app,
        ["user", "show", "heidi", "--config", str(root / "sftpwarden.yaml")],
    )
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["username"] == "heidi"


def test_user_add_runs_real_refresh_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    calls: list[bool] = []

    def fake_refresh_context(entry, *, dry_run=False):
        calls.append(dry_run)
        return f"refreshed {entry.name}"

    monkeypatch.setattr(cli_workflows, "refresh_context", fake_refresh_context)

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "erin",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [False]
    assert "refreshed dev" in result.output


def test_user_update_changes_uid_gid_and_upload_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    password_hash = "$6$rounds=500000$saltstring$hashvalue"
    runner.invoke(
        app,
        [
            "user",
            "add",
            "frank",
            "--password-hash",
            password_hash,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    result = runner.invoke(
        app,
        [
            "user",
            "update",
            "frank",
            "--uid",
            "12001",
            "--gid",
            "12002",
            "--upload-dir",
            "dropbox",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))
    frank = provider["users"][0]

    assert result.exit_code == 0, result.output
    assert frank["uid"] == 12001
    assert frank["gid"] == 12002
    assert frank["upload_dir"] == "dropbox"


def test_user_update_comment_only_does_not_refresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "grace",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    calls: list[bool] = []

    def fake_refresh_context(entry, *, dry_run=False):
        calls.append(dry_run)
        return f"refreshed {entry.name}"

    monkeypatch.setattr(cli_workflows, "refresh_context", fake_refresh_context)

    result = runner.invoke(
        app,
        [
            "user",
            "update",
            "grace",
            "--comment",
            "Archive account",
            "--context",
            "dev",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert calls == []
    assert provider["users"][0]["comment"] == "Archive account"


def test_user_add_rejects_password_and_hash_together(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "dana",
            "--password",
            "correct horse battery staple",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 1
    assert "Use either --password or --password-hash" in result.output


def test_user_remove_can_delete_user_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    data_file = root / "data" / "alice" / "upload" / "payload.txt"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("payload", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "user",
            "remove",
            "alice",
            "--context",
            "dev",
            "--delete-files",
            "--yes",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"] == []
    assert not (root / "data" / "alice").exists()
    assert "Deleted data directory for alice" in result.output
