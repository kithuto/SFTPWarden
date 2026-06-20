from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.prompt import Confirm
from rich.table import Table

from sftpwarden.cli_commands.common import (
    app,
    handle_error,
    print_json,
    print_runtime_plan,
    runtime_plan_explanation,
    runtime_plan_to_json,
)
from sftpwarden.config import (
    load_config,
    provider_local_path,
)
from sftpwarden.contexts import (
    resolve_context,
)
from sftpwarden.providers import (
    load_users_from_project,
)
from sftpwarden.refresh import refresh_context, resolve_refresh_targets
from sftpwarden.remote.deploy import deploy_context
from sftpwarden.render.compose import compose_text, write_compose
from sftpwarden.runtime import (
    RuntimeState,
    build_runtime_plan,
)
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import expand_path
from sftpwarden.watcher import (
    derive_watch_targets,
    poll_watch,
)


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
        table = Table(title=f"Context {entry.name}")
        table.add_column("Field")
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
        console.print(
            f"[green]Valid config[/green] for project [bold]{loaded.project.name}[/bold]."
        )
        console.print(f"Provider: {loaded.provider.type.value} at {provider_path}")
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
            console.print(f"[green]Wrote[/green] {target}")
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

    compose_path = Path(entry.root) / loaded.docker.compose_file
    expected_compose = compose_text(loaded, entry.root)
    if not compose_path.exists():
        reasons.append(f"{loaded.docker.compose_file} is missing")
    elif compose_path.read_text(encoding="utf-8") != expected_compose:
        reasons.append(f"{loaded.docker.compose_file} differs from current configuration")
    return reasons


def print_deploy_config_plan(reasons: list[str]) -> None:
    """Print deploy-level configuration plan details.

    Parameters
    ----------
    reasons
        Detected configuration changes.
    """
    if not reasons:
        console.print("No deploy-level configuration changes detected.")
        return
    console.print(
        "Configuration/deploy changes detected. These changes will be applied by "
        "`sftpwarden deploy`; `sftpwarden refresh` only applies user/provider changes."
    )
    for reason in reasons:
        console.print(f"- {reason}")


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
        console.print(f"Context: [bold]{entry.name}[/bold]")
        console.print(f"Provider users: {len(users.users)}")
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
            output = refresh_context(target, dry_run=dry_run)
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
            console.print(f"{target.context}: {target.local_path} -> {target.remote_path}")
        if dry_run:
            console.print("Dry run only; no files synced.")
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
        console.print("Watching remote local-sync contexts.")
        poll_watch(interval_seconds=interval, dry_run=dry_run)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def deploy(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip critical confirmation.")] = False,
) -> None:
    """Deploy the selected context.

    Parameters
    ----------
    context
        Optional context name.
    dry_run
        Whether to print planned commands without executing them.
    yes
        Whether to skip critical-context confirmation.
    """
    try:
        entry = resolve_context(context_name=context, reconcile_config=True)
        if (
            entry.critical
            and not dry_run
            and not yes
            and not Confirm.ask(f"Deploy critical context {entry.name}?", default=False)
        ):
            raise typer.Exit(1)
        console.print(deploy_context(entry, dry_run=dry_run))
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
    checks = [
        {"name": binary, "available": shutil.which(binary) is not None}
        for binary in ("docker", "ssh", "rsync")
    ]
    if json_output:
        print_json({"checks": checks})
        return
    console.print("SFTPWarden doctor")
    for check in checks:
        status = "available" if check["available"] else "check PATH"
        console.print(f"- {check['name']}: {status}")
