from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sftpwarden.config import DeployTarget, ProviderType, default_project_config
from sftpwarden.contexts import ContextEntry
from sftpwarden.services.provider_transfer import write_provider_users
from sftpwarden.users import ProviderUsers, SFTPUser


def test_sqlite_kubernetes_provider_transfer_reports_manual_action(
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
    memory_provider_factory: Callable[[ProviderUsers], object],
    user_factory: Callable[..., SFTPUser],
) -> None:
    root, entry = local_project_factory(provider=ProviderType.SQLITE)
    config = default_project_config("dev", ProviderType.SQLITE)
    config.deploy.target = DeployTarget.KUBERNETES
    provider = memory_provider_factory(ProviderUsers(users=[]))

    result = write_provider_users(
        entry=entry,
        config=config,
        provider=provider,
        source_users=ProviderUsers(users=[user_factory("alice")]),
        mode="replace",
        dry_run=False,
        no_refresh=False,
    )

    assert root.exists()
    assert result.manual_action is not None
    assert "SQLite provider changes were saved locally only" in result.manual_action
    assert result.refresh_output is None
