from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

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
