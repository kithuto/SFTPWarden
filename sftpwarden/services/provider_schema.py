"""Provider user schema reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sftpwarden.config import SFTPWardenConfig
from sftpwarden.contexts import ContextEntry
from sftpwarden.providers import BaseProvider, provider_from_config
from sftpwarden.users import ProviderUsers
from sftpwarden.users.schemas import (
    migrate_provider_users,
    supported_user_schemas,
    users_to_mapping,
    validate_user_schema_version,
)
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import write_private_text


@dataclass(frozen=True)
class ProviderSchemaReconciliation:
    """Result of comparing provider data with configured schema."""

    changed: bool
    from_schema: int
    to_schema: int
    users: int
    dry_run: bool = False
    backup_path: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return {
            "changed": self.changed,
            "from_schema": self.from_schema,
            "to_schema": self.to_schema,
            "users": self.users,
            "dry_run": self.dry_run,
            "backup_path": self.backup_path,
        }


@dataclass(frozen=True)
class _ProviderSchemaPlan:
    provider: BaseProvider
    source_users: ProviderUsers
    migrated_users: ProviderUsers | None
    result: ProviderSchemaReconciliation


def plan_provider_schema_reconciliation(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    *,
    dry_run: bool = False,
    provider: BaseProvider | None = None,
) -> ProviderSchemaReconciliation:
    """Return whether provider data must move to ``config.provider.user_schema``."""
    return _build_provider_schema_plan(entry, config, dry_run=dry_run, provider=provider).result


def reconcile_provider_schema(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    *,
    dry_run: bool = False,
    backup: bool = True,
    provider: BaseProvider | None = None,
) -> ProviderSchemaReconciliation:
    """Apply the configured provider user schema when a forward migration is needed."""
    plan = _build_provider_schema_plan(entry, config, dry_run=dry_run, provider=provider)
    if not plan.result.changed or dry_run:
        return plan.result

    backup_path = None
    if backup:
        backup_path = write_provider_schema_backup(entry.root, plan.source_users)
    if plan.migrated_users is not None:
        plan.provider.write(plan.migrated_users)
    return ProviderSchemaReconciliation(
        changed=True,
        from_schema=plan.result.from_schema,
        to_schema=plan.result.to_schema,
        users=plan.result.users,
        dry_run=False,
        backup_path=str(backup_path) if backup_path else None,
    )


def write_provider_schema_backup(project_root: str | Path | None, users: ProviderUsers) -> Path:
    """Write a logical YAML backup before changing provider schema."""
    root = Path(project_root) if project_root is not None else Path.cwd()
    from datetime import UTC, datetime

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = root / ".sftpwarden" / "backups" / f"provider-users-{timestamp}.yaml"
    write_private_text(
        backup_path,
        yaml.safe_dump(users_to_mapping(users), sort_keys=False),
    )
    return backup_path


def _build_provider_schema_plan(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    *,
    dry_run: bool,
    provider: BaseProvider | None = None,
) -> _ProviderSchemaPlan:
    if not entry.root:
        raise ProviderError(
            f"Context {entry.name} has no local project root.",
            suggestion="Use a local or remote local-sync context for provider schema changes.",
        )
    target_version = validate_user_schema_version(config.provider.user_schema)
    provider = provider or provider_from_config(entry.root, config)
    source_users = _read_provider_users_for_configured_schema(entry, config, provider)
    source_version = validate_user_schema_version(source_users.schema_version)

    if source_version > target_version:
        raise ProviderError(
            (
                f"Configured provider.user_schema v{target_version} is older than "
                f"provider data schema v{source_version}."
            ),
            suggestion=(
                f"Set provider.user_schema to {source_version}. "
                "Provider schema migrations are forward-only."
            ),
        )
    if source_version == target_version:
        return _ProviderSchemaPlan(
            provider=provider,
            source_users=source_users,
            migrated_users=None,
            result=ProviderSchemaReconciliation(
                changed=False,
                from_schema=source_version,
                to_schema=target_version,
                users=len(source_users.users),
                dry_run=dry_run,
            ),
        )

    migrated_users = migrate_provider_users(source_users, to_version=target_version)
    return _ProviderSchemaPlan(
        provider=provider,
        source_users=source_users,
        migrated_users=migrated_users,
        result=ProviderSchemaReconciliation(
            changed=True,
            from_schema=source_version,
            to_schema=target_version,
            users=len(migrated_users.users),
            dry_run=dry_run,
        ),
    )


def _read_provider_users_for_configured_schema(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    provider: BaseProvider,
) -> ProviderUsers:
    target_version = validate_user_schema_version(config.provider.user_schema)
    if _schema_storage_missing(provider):
        previous_version = _previous_supported_schema(target_version)
        if previous_version is not None:
            previous_provider = _provider_for_schema(entry, config, previous_version)
            if _schema_storage_available(previous_provider):
                return previous_provider.read()
    return provider.read()


def _provider_for_schema(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    schema_version: int,
) -> BaseProvider:
    if not entry.root:
        raise ProviderError(f"Context {entry.name} has no local project root.")
    schema_config = config.model_copy(
        update={"provider": config.provider.model_copy(update={"user_schema": schema_version})}
    )
    return provider_from_config(entry.root, schema_config)


def _schema_storage_missing(provider: BaseProvider) -> bool:
    table_exists = getattr(provider, "table_exists", None)
    if not callable(table_exists):
        return False
    return not bool(table_exists())


def _schema_storage_available(provider: BaseProvider) -> bool:
    table_exists = getattr(provider, "table_exists", None)
    if not callable(table_exists):
        return False
    return bool(table_exists())


def _previous_supported_schema(target_version: int) -> int | None:
    previous_versions = [
        version for version in supported_user_schemas() if version < target_version
    ]
    if not previous_versions:
        return None
    return max(previous_versions)
