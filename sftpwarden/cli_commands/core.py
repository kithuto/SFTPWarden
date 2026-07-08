"""Core SFTPWarden CLI commands for contexts, deploys, health, and backups."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.prompt import Confirm
from rich.table import Table

from sftpwarden.cli_commands.app import app
from sftpwarden.cli_commands.deploy_schema import (
    apply_provider_schema_before_deploy,
    provider_schema_deploy_text,
)
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import (
    print_deploy_config_plan,
    print_json,
    print_runtime_plan,
    runtime_plan_explanation,
    runtime_plan_to_json,
)
from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    load_config,
    provider_local_path,
)
from sftpwarden.contexts import (
    require_initialized_context,
    resolve_context,
)
from sftpwarden.providers import (
    load_users_from_project,
)
from sftpwarden.refresh import refresh_context, resolve_refresh_targets
from sftpwarden.render.compose import compose_text, write_compose
from sftpwarden.runtime import (
    RuntimeState,
    build_runtime_plan,
)
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.services.deploy import (
    apply_deployment_plan,
    deployment_plan,
    helm_values_diff_reason,
    kubernetes_rendered_manifest_diff_reason,
)
from sftpwarden.services.health import project_health
from sftpwarden.utils.console import console, print_info, print_success, terminal_status
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import expand_path
from sftpwarden.utils.platform import system_is
from sftpwarden.watcher import (
    derive_watch_targets,
    poll_watch,
)


def deploy_context(entry, *, dry_run: bool = False) -> str:
    """Compatibility wrapper for deploy command execution."""
    plan_data = deployment_plan(entry)
    if dry_run:
        return plan_data.text()
    return apply_deployment_plan(entry)


@app.command()
def info(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show resolved context information.

    Parameters
    ----------
    context
        Optional context name.
    config
        Optional direct config path.
    json_output
        Whether to emit JSON instead of a table.
    """
    try:
        entry = resolve_context(config_path=config, context_name=context)
        if json_output:
            print_json(entry.model_dump_json(indent=2))
            return
        table = Table(title=f"Context {entry.name}", box=box.SIMPLE_HEAVY, header_style="bold cyan")
        table.add_column("Field", style="bold")
        table.add_column("Value")
        for key, value in entry.model_dump(mode="json", exclude_none=True).items():
            table.add_row(key, json.dumps(value) if isinstance(value, dict) else str(value))
        console.print(table)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def validate(
    config: Annotated[str, typer.Option("--config", help="Config file path.")] = "sftpwarden.yaml",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate a project config and provider location.

    Parameters
    ----------
    config
        Config file path to validate.
    json_output
        Whether to emit machine-readable JSON.
    """
    try:
        config_path = expand_path(config)
        loaded = load_config(config_path)
        provider_path = provider_local_path(config_path.parent, loaded)
        if json_output:
            print_json(
                {
                    "valid": True,
                    "project": loaded.project.name,
                    "provider": loaded.provider.type.value,
                    "config_path": str(config_path),
                    "provider_path": str(provider_path),
                }
            )
            return
        print_success(f"Valid config for project [bold]{loaded.project.name}[/bold].")
        print_info(f"Provider [bold]{loaded.provider.type.value}[/bold] at {provider_path}")
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def compose(
    config: Annotated[str, typer.Option("--config")] = "sftpwarden.yaml",
    write: Annotated[bool, typer.Option("--write", help="Write docker-compose.yml.")] = False,
) -> None:
    """Render or write the Docker Compose file for a project.

    Parameters
    ----------
    config
        Config file path.
    write
        Whether to write ``docker-compose.yml`` instead of printing it.
    """
    try:
        path = expand_path(config)
        loaded = load_config(path)
        if write:
            target = write_compose(loaded, path.parent)
            print_success(f"Wrote {target}")
            return
        console.print(compose_text(loaded, path.parent))
    except SFTPWardenError as exc:
        handle_error(exc)


def deploy_config_change_reasons(entry, loaded) -> list[str]:
    """Return deploy-level configuration changes detected for a context.

    Parameters
    ----------
    entry
        Resolved context entry.
    loaded
        Loaded project config.

    Returns
    -------
    list[str]
        Human-readable reasons that require ``sftpwarden deploy``.
    """
    reasons: list[str] = []
    if entry.name != loaded.project.name:
        reasons.append("project.name differs from the registered context")
    if entry.provider != loaded.provider.type:
        reasons.append("provider type differs from the registered context")

    root = Path(entry.root)
    if loaded.deploy.target == DeployTarget.KUBERNETES:
        reasons.append(f"deploy target is {loaded.deploy.target.value}")
        reasons.append(f"kubernetes mode is {loaded.kubernetes.mode.value}")
        if loaded.kubernetes.mode == KubernetesMode.HELM:
            helm_reason = helm_values_diff_reason(loaded, root)
            if helm_reason:
                reasons.append(helm_reason)
        else:
            manifest_reason = kubernetes_rendered_manifest_diff_reason(loaded, root)
            if manifest_reason:
                reasons.append(manifest_reason)
        return reasons

    compose_path = root / loaded.docker.compose_file
    expected_compose = compose_text(loaded, entry.root)
    if not compose_path.exists():
        reasons.append(f"{loaded.docker.compose_file} is missing")
    elif compose_path.read_text(encoding="utf-8") != expected_compose:
        reasons.append(f"{loaded.docker.compose_file} differs from current configuration")
    return reasons


@app.command()
def plan(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the runtime sync plan for a local context.

    Parameters
    ----------
    context
        Optional context name.
    config
        Optional direct config path.
    json_output
        Whether to emit the plan as JSON.
    """
    try:
        entry = resolve_context(config_path=config, context_name=context)
        if not entry.root or not entry.config:
            raise SFTPWardenError(
                f"Context {entry.name} has no local config to plan.",
                suggestion="Use `sftpwarden refresh --dry-run` for remote-only contexts.",
            )
        loaded = load_config(entry.config)
        users = load_users_from_project(entry.root, loaded)
        state = RuntimeState.load(Path(entry.root) / "state" / "state.json")
        runtime_plan = build_runtime_plan(loaded, users, state)
        config_change_reasons = deploy_config_change_reasons(entry, loaded)
        if json_output:
            data = json.loads(runtime_plan_to_json(runtime_plan))
            data["deploy_config_changed"] = bool(config_change_reasons)
            data["deploy_config_reasons"] = config_change_reasons
            print_json(data)
            return
        print_info(f"Context [bold]{entry.name}[/bold]")
        print_info(f"Provider users [bold]{len(users.users)}[/bold]")
        console.print(runtime_plan_explanation(runtime_plan, apply_command="sftpwarden refresh"))
        console.print(runtime_plan.summary())
        print_deploy_config_plan(config_change_reasons)
        print_runtime_plan(runtime_plan)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def refresh(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    all_contexts: Annotated[bool, typer.Option("--all")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Refresh one or more contexts.

    Parameters
    ----------
    context
        Optional context name.
    config
        Optional direct config path.
    all_contexts
        Whether to refresh all registered contexts.
    dry_run
        Whether to print commands without executing them.
    json_output
        Whether to emit machine-readable JSON.
    """
    try:
        targets = resolve_refresh_targets(
            all_contexts=all_contexts, context_name=context, config_path=config
        )
        results = []
        for target in targets:
            if dry_run:
                output = refresh_context(target, dry_run=True)
            else:
                with terminal_status(f"Refreshing context {target.name}"):
                    output = refresh_context(target)
            results.append({"context": target.name, "result": output})
            if not json_output:
                console.print(output)
        if json_output:
            print_json({"dry_run": dry_run, "targets": results})
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def sync(
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List or dry-run watcher sync targets.

    Parameters
    ----------
    dry_run
        Whether to avoid file synchronization.
    json_output
        Whether to emit sync targets as JSON.
    """
    try:
        require_initialized_context()
        targets = derive_watch_targets()
        if json_output:
            print_json(
                {
                    "dry_run": dry_run,
                    "targets": [
                        {
                            "context": target.context,
                            "local_path": str(target.local_path),
                            "remote_path": target.remote_path,
                        }
                        for target in targets
                    ],
                }
            )
            return
        for target in targets:
            console.print(
                f"[bold]{target.context}[/bold]: {target.local_path} [cyan]->[/cyan] "
                f"{target.remote_path}"
            )
        if dry_run:
            print_info("Dry run only; no files synced.")
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def watch(
    interval: Annotated[int, typer.Option("--interval", min=1)] = 2,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Poll local files and sync changed watcher targets.

    Parameters
    ----------
    interval
        Polling interval in seconds.
    dry_run
        Whether to report changes without syncing files.
    """
    try:
        require_initialized_context()
        print_info("Watching remote local-sync contexts.")
        poll_watch(interval_seconds=interval, dry_run=dry_run)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def deploy(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip critical confirmation.")] = False,
) -> None:
    """Deploy the selected context.

    Parameters
    ----------
    context
        Optional context name.
    dry_run
        Whether to print planned commands without executing them.
    json_output
        Whether to emit machine-readable JSON.
    yes
        Whether to skip critical-context confirmation.
    """
    try:
        entry = resolve_context(context_name=context, reconcile_config=True)
        if (
            not dry_run
            and entry.critical
            and not yes
            and not Confirm.ask(f"Deploy critical context {entry.name}?", default=False)
        ):
            raise typer.Exit(1)
        deploy_config = load_config(entry.config) if entry.config else None
        schema_result = (
            apply_provider_schema_before_deploy(entry, deploy_config, dry_run=dry_run, yes=yes)
            if deploy_config is not None
            else None
        )
        plan_data = deployment_plan(entry)
        if dry_run:
            if json_output:
                print_json(
                    {
                        "dry_run": True,
                        "plan": plan_data.as_dict(),
                        "provider_schema": (
                            schema_result.as_dict() if schema_result is not None else None
                        ),
                    }
                )
                return
            schema_text = provider_schema_deploy_text(schema_result)
            if schema_text:
                console.print(schema_text)
            console.print(plan_data.text())
            return
        if schema_result and schema_result.changed:
            print_success(provider_schema_deploy_text(schema_result))
            if schema_result.backup_path:
                console.print(f"Backup: {schema_result.backup_path}")
        with terminal_status(f"Deploying context {entry.name}"):
            output = deploy_context(entry)
        if json_output:
            print_json(
                {
                    "dry_run": False,
                    "result": output,
                    "plan": plan_data.as_dict(),
                    "provider_schema": (
                        schema_result.as_dict() if schema_result is not None else None
                    ),
                }
            )
            return
        print_success(output)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def doctor(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Check external command availability.

    Parameters
    ----------
    json_output
        Whether to emit machine-readable JSON.
    """
    sync_binary = "scp" if system_is("Windows") else "rsync"
    checks = [
        {
            "name": binary,
            "available": shutil.which(binary) is not None,
            "required_for": required_for,
        }
        for binary, required_for in (
            ("docker", "Docker Compose deployments"),
            ("ssh", "remote contexts"),
            (sync_binary, "remote local-sync contexts"),
            ("kubectl", "Kubernetes manifest deployments"),
            ("helm", "Helm deployments"),
        )
    ]
    if json_output:
        print_json({"checks": checks})
        return
    console.print("[bold]SFTPWarden doctor[/bold]")
    for check in checks:
        status = "[green]available[/green]" if check["available"] else "[yellow]check PATH[/yellow]"
        console.print(
            f"  [cyan]-[/cyan] [bold]{check['name']}[/bold]: {status} ({check['required_for']})"
        )


@app.command()
def health(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check project and runtime health.

    Parameters
    ----------
    context
        Optional context name.
    json_output
        Whether to emit JSON.
    """
    try:
        report = project_health(context)
        if json_output:
            print_json(report.as_dict())
            raise typer.Exit(0 if report.healthy else 1)
        table = Table(
            title=f"Health for {report.context}",
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
        )
        table.add_column("Check", style="bold")
        table.add_column("Status")
        table.add_column("Message")
        table.add_column("Fix")
        for check in report.checks:
            style = {"pass": "green", "warn": "yellow", "fail": "red"}[check.status]
            table.add_row(
                check.name,
                f"[{style}]{check.status}[/{style}]",
                check.message,
                check.suggestion or "",
            )
        console.print(table)
        raise typer.Exit(0 if report.healthy else 1)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def backup(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    output: Annotated[str | None, typer.Option("--output", "-o")] = None,
    include_data: Annotated[bool, typer.Option("--include-data")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Create a project backup.

    Parameters
    ----------
    context
        Optional context name.
    output
        Optional output archive path.
    include_data
        Whether to include SFTP user data.
    dry_run
        Whether to avoid writing.
    json_output
        Whether to emit JSON.
    yes
        Whether to skip include-data confirmation.
    """
    try:
        if (
            include_data
            and not yes
            and not dry_run
            and not Confirm.ask(
                "Include SFTP user data in the backup?",
                default=False,
            )
        ):
            raise typer.Exit(1)
        result = create_backup(
            context_name=context,
            output=output,
            include_data=include_data,
            dry_run=dry_run,
        )
        data = {
            "path": str(result.path),
            "entries": result.entries,
            "dry_run": dry_run,
        }
        if json_output:
            print_json(data)
            return
        if dry_run:
            print_info(f"Backup would be written to [bold]{result.path}[/bold].")
        else:
            print_success(f"Wrote backup [bold]{result.path}[/bold].")
        for entry in result.entries:
            console.print(f"  [cyan]-[/cyan] {entry}")
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def restore(
    backup_path: Annotated[str, typer.Argument(help="Backup archive path.")],
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    include_data: Annotated[bool, typer.Option("--include-data")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Restore a project backup.

    Parameters
    ----------
    backup_path
        Backup archive path.
    context
        Optional context name.
    include_data
        Whether to restore SFTP user data.
    dry_run
        Whether to avoid writing.
    json_output
        Whether to emit JSON.
    yes
        Whether to skip confirmation.
    """
    try:
        if (
            not yes
            and not dry_run
            and not Confirm.ask(
                "Restore backup and overwrite project files?",
                default=False,
            )
        ):
            raise typer.Exit(1)
        if (
            include_data
            and not yes
            and not dry_run
            and not Confirm.ask(
                "Restore SFTP user data from the backup?",
                default=False,
            )
        ):
            raise typer.Exit(1)
        result = restore_backup(
            context_name=context,
            backup_path=backup_path,
            include_data=include_data,
            dry_run=dry_run,
        )
        data = {
            "path": str(result.path),
            "entries": result.entries,
            "safety_backup": str(result.safety_backup) if result.safety_backup else None,
            "dry_run": dry_run,
        }
        if json_output:
            print_json(data)
            return
        if dry_run:
            print_info(f"Backup [bold]{result.path}[/bold] can be restored.")
        else:
            print_success(f"Restored backup [bold]{result.path}[/bold].")
            if result.safety_backup:
                print_info(f"Safety backup: [bold]{result.safety_backup}[/bold]")
        for entry in result.entries:
            console.print(f"  [cyan]-[/cyan] {entry}")
    except SFTPWardenError as exc:
        handle_error(exc)
