"""CLI helpers for applying provider schema config during deploy operations."""

from __future__ import annotations

import typer
from rich.prompt import Confirm

from sftpwarden.config import SFTPWardenConfig
from sftpwarden.contexts import ContextEntry
from sftpwarden.services.provider_schema import (
    ProviderSchemaReconciliation,
    plan_provider_schema_reconciliation,
    reconcile_provider_schema,
)


def provider_schema_deploy_text(result: ProviderSchemaReconciliation | None) -> str:
    """Return human-readable text for a provider schema deploy step."""
    if result is None or not result.changed:
        return ""
    action = "Would migrate" if result.dry_run else "Migrated"
    return (
        f"{action} provider schema v{result.from_schema} -> v{result.to_schema} "
        f"({result.users} user(s))."
    )


def apply_provider_schema_before_deploy(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    *,
    dry_run: bool,
    yes: bool,
) -> ProviderSchemaReconciliation | None:
    """Apply pending provider schema config before deployment commands."""
    if not entry.config or not entry.root:
        return None
    planned = plan_provider_schema_reconciliation(entry, config, dry_run=dry_run)
    if not planned.changed or dry_run:
        return planned
    if not yes and not Confirm.ask(
        (f"Migrate provider schema v{planned.from_schema} -> v{planned.to_schema} before deploy?"),
        default=False,
    ):
        raise typer.Exit(1)
    return reconcile_provider_schema(entry, config, dry_run=False, backup=True)
