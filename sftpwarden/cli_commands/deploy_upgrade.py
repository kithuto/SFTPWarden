"""Shared upgrade presentation for explicit deployment commands."""

from __future__ import annotations

import typer
from rich.prompt import Confirm

from sftpwarden.cli_commands.output import print_json
from sftpwarden.config import KubernetesMode, load_config
from sftpwarden.contexts import ContextEntry
from sftpwarden.services.deployment_upgrade import UpgradePlan, plan_upgrade
from sftpwarden.services.provider_schema import plan_provider_schema_reconciliation
from sftpwarden.utils.console import console


def prepare_upgrade(
    entry: ContextEntry,
    *,
    dry_run: bool,
    yes: bool,
    json_output: bool,
    kubernetes_mode: KubernetesMode | None = None,
) -> UpgradePlan:
    """Inspect the target and obtain one explicit deployment confirmation."""
    plan = plan_upgrade(entry, kubernetes_mode=kubernetes_mode)
    schema = (
        plan_provider_schema_reconciliation(entry, load_config(entry.config), dry_run=True)
        if entry.config and entry.root
        else None
    )
    if not json_output:
        console.print(
            f"Deployment: {plan.source or 'not installed'} -> {plan.target} ({plan.kind})."
        )
        console.print(plan.backup_advice)
        if schema and schema.changed:
            console.print(
                f"Provider schema will migrate from v{schema.from_schema} to v{schema.to_schema}."
            )
        if not plan.managed_image:
            console.print(
                "Custom runtime image will be preserved; its maintainer controls runtime updates."
            )
    if (
        not dry_run
        and not yes
        and (entry.critical or plan.kind == "upgrade" or (schema and schema.changed))
    ):
        if json_output:
            print_json(
                {
                    "status": "confirmation_required",
                    "upgrade": plan.as_dict(),
                    "provider_schema": schema.as_dict() if schema else None,
                }
            )
            raise typer.Exit(1)
        if not Confirm.ask(
            "Deploy this update? Active transfers may be interrupted.", default=False
        ):
            raise typer.Exit(1)
    return plan
