from __future__ import annotations

from pathlib import Path

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


def test_user_add_updates_yaml_without_plaintext_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKeyForTestsOnly00000000000000000000000000"
    result = runner.invoke(
        app,
        ["user", "add", "alice", "--public-key", key, "--context", "dev", "--no-refresh"],
    )

    assert result.exit_code == 0, result.output
    assert "alice" in (root / "users.yaml").read_text(encoding="utf-8")
