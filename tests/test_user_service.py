from __future__ import annotations

from pathlib import Path

import pytest

from sftpwarden.config import default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import empty_provider_text
from sftpwarden.services.users import UserService
from sftpwarden.utils.errors import ProviderError

PASSWORD_HASH = "$6$rounds=500000$saltstring$hashvalue"


def create_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = local_context("dev", root, config.provider.type)
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))
    return root


def test_user_service_add_show_update_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_project(tmp_path, monkeypatch)
    service = UserService(context_name="dev")

    service.add_user(username="alice", password_hash=PASSWORD_HASH, comment="Initial")
    shown = service.show_user("alice")
    result = service.update_user("alice", comment="Accounting")

    assert shown.username == "alice"
    assert result.user.comment == "Accounting"
    assert result.runtime_changed is False
    assert service.list_users().users[0].comment == "Accounting"

    service.remove_user("alice")
    with pytest.raises(ProviderError, match="Unknown user"):
        service.show_user("alice")


def test_user_service_runtime_update_reports_refresh_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_project(tmp_path, monkeypatch)
    service = UserService(context_name="dev")
    service.add_user(username="alice", password_hash=PASSWORD_HASH)

    result = service.update_user("alice", uid=12001)

    assert result.user.uid == 12001
    assert result.runtime_changed is True
