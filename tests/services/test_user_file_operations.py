from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.services.users as user_services
from sftpwarden.cli import app
from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import (
    ContextRegistry,
    remote_context,
    save_registry,
)
from sftpwarden.services.users import UserService
from sftpwarden.utils.errors import RuntimeError

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


def test_user_service_delete_file_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    runner = CliRunner()
    result = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output
    service = UserService(context_name="dev")

    assert service.delete_user_files("missing") == "No data directory found for missing."
    data_dir = root / "data" / "alice"
    data_dir.mkdir(parents=True)
    (data_dir / "file.txt").write_text("data", encoding="utf-8")
    assert "Deleted data directory" in service.delete_user_files("alice")
    assert not data_dir.exists()
    with pytest.raises(RuntimeError, match="Invalid username"):
        service.delete_user_files("../bad")


def test_user_service_remote_delete_uses_safe_remote_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("prod")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text("users: []\n", encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    commands: list[list[str]] = []
    monkeypatch.setattr(
        user_services,
        "run_checked",
        lambda command, **_kwargs: commands.append(command),
    )

    message = UserService(context_name="prod").delete_user_files("alice")

    assert message == "Deleted remote data directory for alice: /opt/sftpwarden/data/alice"
    assert commands
    assert "rm -rf -- /opt/sftpwarden/data/alice" in commands[0][-1]


def test_user_service_remote_delete_requires_remote_settings(tmp_path: Path) -> None:
    entry = remote_context(
        name="broken",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    entry.remote = None
    service = object.__new__(UserService)
    service.entry = entry

    with pytest.raises(RuntimeError, match="missing remote settings"):
        service.delete_user_files("alice")
