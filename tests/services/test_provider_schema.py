from __future__ import annotations

from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import local_context
from sftpwarden.contexts.registry import ContextEntry, ContextType
from sftpwarden.providers import provider_from_config
from sftpwarden.services.provider_schema import (
    _previous_supported_schema,
    _provider_for_schema,
    _schema_storage_available,
    plan_provider_schema_reconciliation,
    reconcile_provider_schema,
)
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError

TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"


def test_provider_schema_reconciliation_migrates_sqlite_v1_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev", ProviderType.SQLITE, user_schema=1)
    write_config(root / "sftpwarden.yaml", config)
    provider_from_config(root, config).write(
        ProviderUsers(
            schema_version=1,
            users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
        )
    )
    target_config = config.model_copy(
        update={"provider": config.provider.model_copy(update={"user_schema": 2})}
    )
    entry = local_context("dev", root, ProviderType.SQLITE)

    planned = plan_provider_schema_reconciliation(entry, target_config)
    result = reconcile_provider_schema(entry, target_config, backup=False)
    loaded = provider_from_config(root, target_config).read()

    assert planned.changed
    assert planned.from_schema == 1
    assert result.changed
    assert loaded.schema_version == 2
    assert loaded.users[0].keys[0].name.startswith("legacy-")


def test_provider_schema_reconciliation_rejects_configured_downgrade(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev", ProviderType.YAML, user_schema=2)
    write_config(root / "sftpwarden.yaml", config)
    provider_from_config(root, config).write(
        ProviderUsers(
            schema_version=2,
            users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
        )
    )
    target_config = config.model_copy(
        update={"provider": config.provider.model_copy(update={"user_schema": 1})}
    )
    entry = local_context("dev", root, ProviderType.YAML)

    with pytest.raises(ProviderError, match="older than provider data schema"):
        plan_provider_schema_reconciliation(entry, target_config)


def test_provider_schema_reconciliation_requires_local_root() -> None:
    config = default_project_config("remote", ProviderType.YAML, user_schema=2)
    entry = ContextEntry(name="remote", type=ContextType.REMOTE)

    with pytest.raises(ProviderError, match="no local project root"):
        plan_provider_schema_reconciliation(entry, config)

    with pytest.raises(ProviderError, match="no local project root"):
        _provider_for_schema(entry, config, 1)


def test_provider_schema_helper_edges_without_versioned_storage() -> None:
    class ProviderWithoutVersionedStorage:
        pass

    assert not _schema_storage_available(ProviderWithoutVersionedStorage())  # type: ignore[arg-type]
    assert _previous_supported_schema(1) is None
