from __future__ import annotations

from pathlib import Path

import pytest

from sftpwarden.config import default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import empty_provider_text
from sftpwarden.services.users import UserService
from sftpwarden.utils.errors import ProviderError, RuntimeError

PASSWORD_HASH = "$6$rounds=500000$saltstring$hashvalue"
TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"
SECOND_TEST_KEY = "ssh-ed25519 ZmFrZS1rZXktMg=="


def create_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_schema: int = 1,
) -> Path:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev", user_schema=user_schema)
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(
        empty_provider_text(config.provider.type, user_schema=user_schema),
        encoding="utf-8",
    )
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
    assert not result.runtime_changed
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
    assert result.runtime_changed


def test_user_key_advanced_operation_migrates_v1_to_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_project(tmp_path, monkeypatch, user_schema=1)
    service = UserService(context_name="dev")
    service.add_user(username="alice", public_keys=[TEST_KEY])
    key_name = service.list_user_keys("alice")[0].name

    result = service.disable_user_key(
        "alice",
        key_name,
        disabled=True,
        allow_migration=True,
    )
    users = service.list_users()

    assert result.schema_migrated
    assert users.schema_version == 2
    assert users.users[0].keys[0].disabled
    assert "schema_version: 2" in (tmp_path / "project" / "users.yaml").read_text(encoding="utf-8")


def test_user_service_named_key_lifecycle_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_project(tmp_path, monkeypatch, user_schema=2)
    service = UserService(context_name="dev")
    service.add_user(username="alice", password_hash=PASSWORD_HASH)
    added = service.add_user_key(
        "alice",
        key_name="prod-ci",
        public_key=TEST_KEY,
        comment="initial",
    )

    shown = service.show_user_key("alice", "prod-ci")
    disabled = service.disable_user_key("alice", "prod-ci", disabled=True)
    enabled = service.disable_user_key("alice", "prod-ci", disabled=False)
    renamed = service.rename_user_key("alice", "prod-ci", "prod-renamed")
    rotated = service.rotate_user_key("alice", "prod-renamed", public_key=TEST_KEY)
    expired = service.expire_user_key("alice", "prod-renamed", expires_at="2027-01-01")
    imported = service.import_user_keys("alice", [("laptop", SECOND_TEST_KEY)])
    removed = service.remove_user_key("alice", "laptop")

    assert added.user.keys[0].name == "prod-ci"
    assert shown.name == "prod-ci"
    assert disabled.user.keys[0].disabled
    assert not enabled.user.keys[0].disabled
    assert renamed.user.keys[0].name == "prod-renamed"
    assert rotated.user.keys[0].fingerprint == shown.fingerprint
    assert expired.user.keys[0].expires_at is not None
    assert imported.user.keys[-1].source == "user.key.import"
    assert all(key.name != "laptop" for key in removed.user.keys)


def test_user_service_public_key_update_and_find_key_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_project(tmp_path, monkeypatch, user_schema=2)
    service = UserService(context_name="dev")
    service.add_user(username="alice", password_hash=PASSWORD_HASH)

    updated = service.update_user("alice", public_keys=[TEST_KEY])

    assert updated.runtime_changed
    assert updated.user.keys[0].source == "user.update"
    with pytest.raises(RuntimeError, match="Unknown key"):
        service.show_user_key("alice", "missing")
