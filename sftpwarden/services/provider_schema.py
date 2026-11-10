"""Provider user schema reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from sftpwarden.config import SFTPWardenConfig, load_config
from sftpwarden.contexts import ContextEntry
from sftpwarden.providers import BaseProvider, provider_from_config
from sftpwarden.providers.schema import read_schema_snapshot
from sftpwarden.services.project_config import update_project_field
from sftpwarden.services.schema_transition import (
    check_transition_config,
    pending_transition,
    publish_schema_transition,
    resume_schema_transition,
)
from sftpwarden.users import ProviderUsers
from sftpwarden.users.schemas import (
    migrate_provider_users,
    supported_user_schemas,
    users_to_mapping,
    validate_user_schema_version,
)
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import write_private_text
from sftpwarden.utils.state_transactions import state_transaction


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
    if entry.root and entry.config:
        pending = pending_transition(entry)
        if pending is not None:
            check_transition_config(pending, config)
            if config.provider.user_schema != pending.target.schema_version:
                raise ProviderError(
                    "A provider schema migration is incomplete.",
                    suggestion="Retry `sftpwarden provider schema migrate --to 2`.",
                )
            return ProviderSchemaReconciliation(
                changed=True,
                from_schema=pending.source.schema_version,
                to_schema=pending.target.schema_version,
                users=len(pending.target.users),
                dry_run=dry_run,
                backup_path=pending.backup_path,
            )
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
    if dry_run:
        return plan_provider_schema_reconciliation(entry, config, dry_run=True, provider=provider)
    if not entry.config or not entry.root:
        raise ProviderError("Schema migration requires a local project configuration.")
    with state_transaction(Path(entry.config)):
        current = load_config(entry.config)
        if current.provider.model_dump(exclude={"user_schema"}) != config.provider.model_dump(
            exclude={"user_schema"}
        ):
            raise ProviderError("Provider configuration changed before schema migration.")
        return _reconcile_provider_schema(entry, config, backup=backup, provider=provider)


def _reconcile_provider_schema(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    *,
    backup: bool,
    provider: BaseProvider | None,
) -> ProviderSchemaReconciliation:
    """Reconcile under the same lock used by user and config writers."""
    if pending_transition(entry) is not None:
        plan_provider_schema_reconciliation(entry, config, provider=provider)
        resumed = resume_schema_transition(entry, config)
        return ProviderSchemaReconciliation(
            changed=True,
            from_schema=resumed.source.schema_version,
            to_schema=resumed.target.schema_version,
            users=len(resumed.target.users),
            backup_path=resumed.backup_path,
        )
    plan = _build_provider_schema_plan(entry, config, dry_run=False, provider=provider)
    current_config = load_config(entry.config)
    if not plan.result.changed:
        if current_config.provider.user_schema != config.provider.user_schema:
            update_project_field(
                entry.config,
                "provider.user_schema",
                config.provider.user_schema,
                expected=current_config,
            )
        return plan.result

    backup_path = None
    if backup:
        backup_path = write_provider_schema_backup(entry.root, plan.source_users)
    if plan.migrated_users is not None:
        publish_schema_transition(
            entry,
            current_config,
            plan.source_users,
            plan.migrated_users,
            provider=plan.provider,
            backup_path=str(backup_path) if backup_path else None,
        )
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
    filename = f"provider-users-{timestamp}-{uuid4().hex}.yaml"
    backup_path = root / ".sftpwarden" / "backups" / filename
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
    """Build the internal plan for reconciling provider user schemas."""
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
    """Read users, falling back to existing prior-schema storage."""
    target_version = validate_user_schema_version(config.provider.user_schema)
    if _schema_storage_missing(provider):
        previous_version = _previous_supported_schema(target_version)
        if previous_version is not None:
            previous_provider = _provider_for_schema(entry, config, previous_version)
            if _schema_storage_available(previous_provider):
                return previous_provider.read()
    return read_schema_snapshot(provider)


def _provider_for_schema(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    schema_version: int,
) -> BaseProvider:
    """Build a provider configured for a specific user schema version."""
    if not entry.root:
        raise ProviderError(f"Context {entry.name} has no local project root.")
    schema_config = config.model_copy(
        update={"provider": config.provider.model_copy(update={"user_schema": schema_version})}
    )
    return provider_from_config(entry.root, schema_config)


def _schema_storage_missing(provider: BaseProvider) -> bool:
    """Return whether a provider reports missing schema storage."""
    table_exists = getattr(provider, "table_exists", None)
    if not callable(table_exists):
        return False
    return not bool(table_exists())


def _schema_storage_available(provider: BaseProvider) -> bool:
    """Return whether a provider reports available schema storage."""
    table_exists = getattr(provider, "table_exists", None)
    if not callable(table_exists):
        return False
    return bool(table_exists())


def _previous_supported_schema(target_version: int) -> int | None:
    """Return the nearest supported schema below the target version."""
    previous_versions = [
        version for version in supported_user_schemas() if version < target_version
    ]
    if not previous_versions:
        return None
    return max(previous_versions)
