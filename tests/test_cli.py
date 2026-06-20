from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

import sftpwarden.services.cli_workflows as cli_workflows
from sftpwarden.cli import app
from sftpwarden.config import load_config


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
