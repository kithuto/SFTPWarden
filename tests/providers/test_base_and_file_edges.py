from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.providers.registry as provider_registry
from sftpwarden.config import ProviderConfig, ProviderType, default_project_config
from sftpwarden.providers import (
    ProviderUsers,
    empty_provider_text,
    load_users,
    provider_for_config,
    provider_for_project,
    provider_local,
    save_users,
)
from sftpwarden.providers import mutations as provider_mutations
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import provider_class, registered_providers
from sftpwarden.users import SFTPUser
from sftpwarden.utils.errors import ProviderError

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


def test_provider_wrapper_functions_round_trip_yaml(tmp_path: Path) -> None:
    path = tmp_path / "users.yaml"
    users = ProviderUsers(users=[SFTPUser(username="alice", password_hash=TEST_HASH)])

    save_users(ProviderType.YAML, path, users)
    loaded = load_users(ProviderType.YAML, path)
    config = default_project_config("dev")
    project_provider = provider_for_project(tmp_path, config)
    direct_provider = provider_for_config(ProviderConfig(type=ProviderType.YAML), path)
    local = provider_local(tmp_path, config)

    assert loaded.users[0].username == "alice"
    assert project_provider.path == tmp_path / "users.yaml"
    assert direct_provider.path == path
    assert local.path == tmp_path / "users.yaml"
    assert ProviderType.YAML in registered_providers()


def test_provider_registry_reports_unknown_registered_provider() -> None:
    class UnregisteredProvider:
        provider_type = ProviderType.YAML

    with pytest.raises(ValueError):
        provider_class("unknown")


def test_file_provider_requires_path_and_empty_file_round_trips(tmp_path: Path) -> None:
    provider = provider_for_config(ProviderConfig(type=ProviderType.YAML))
    with pytest.raises(ProviderError, match="requires a path"):
        provider.read()
    with pytest.raises(ProviderError, match="Provider file not found"):
        provider_for_config(
            ProviderConfig(type=ProviderType.YAML), tmp_path / "missing.yaml"
        ).read()

    path = tmp_path / "empty.yaml"
    path.write_text(empty_provider_text(ProviderType.YAML), encoding="utf-8")
    assert load_users(ProviderType.YAML, path).users == []
    nested = tmp_path / "nested" / "users.yaml"
    provider_for_config(ProviderConfig(type=ProviderType.YAML), nested).write(
        ProviderUsers(users=[])
    )
    assert nested.exists()

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("users:\n  - username: BadUser\n", encoding="utf-8")
    with pytest.raises(ProviderError, match="Invalid YAML"):
        load_users(ProviderType.YAML, bad_yaml)

    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(
        "username,public_keys,password_hash,uid,gid,upload_dir,comment,disabled\n"
        f"alice,,{TEST_HASH},,,,false\n"
        f"alice,,{TEST_HASH},,,,false\n",
        encoding="utf-8",
    )
    with pytest.raises(ProviderError, match="Invalid CSV"):
        load_users(ProviderType.CSV, bad_csv)


def test_base_provider_defaults_and_registry_errors() -> None:
    class MinimalProvider(BaseProvider):
        provider_type = ProviderType.YAML

        @classmethod
        def empty_text(cls) -> str:
            return super().empty_text()

        def read(self) -> ProviderUsers:
            return super().read()

        def write(self, users: ProviderUsers) -> None:
            super().write(users)

    provider = MinimalProvider(ProviderConfig(type=ProviderType.YAML))

    with pytest.raises(NotImplementedError):
        MinimalProvider.empty_text()
    with pytest.raises(NotImplementedError):
        provider.read()
    with pytest.raises(NotImplementedError):
        provider.write(ProviderUsers(users=[]))
    original = provider_registry._PROVIDERS.copy()
    try:
        provider_registry._PROVIDERS.pop(ProviderType.YAML, None)
        with pytest.raises(ProviderError, match="not registered"):
            provider_class("yaml")
    finally:
        provider_registry._PROVIDERS.clear()
        provider_registry._PROVIDERS.update(original)
    assert provider_mutations.users_fingerprint(ProviderUsers(users=[]))
