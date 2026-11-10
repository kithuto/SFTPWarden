from __future__ import annotations

from typing import Annotated

import typer

from sftpwarden.cli_commands.app import runtime_app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import (
    print_json,
    print_runtime_plan,
    runtime_plan_explanation,
    runtime_plan_to_json,
)
from sftpwarden.runtime import (
    build_runtime_plan,
    load_runtime_inputs,
    run_sync_loop,
)
from sftpwarden.runtime.configuration import check_desired_provider
from sftpwarden.runtime.execution import execute_pass as apply_once
from sftpwarden.runtime.probes import Probe
from sftpwarden.services.health import runtime_health as runtime_health_report
from sftpwarden.utils.console import console, terminal_status
from sftpwarden.utils.errors import SFTPWardenError


@runtime_app.command("refresh")
def runtime_refresh(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
    desired_provider_fingerprint: Annotated[
        str | None, typer.Option("--desired-provider-fingerprint", hidden=True)
    ] = None,
    desired_config_fingerprint: Annotated[
        str | None, typer.Option("--desired-config-fingerprint", hidden=True)
    ] = None,
) -> None:
    """Force one runtime synchronization pass.

    Parameters
    ----------
    config
        Runtime config path inside the container.
    """
    try:
        if desired_provider_fingerprint is not None or desired_config_fingerprint is not None:
            deploy_required = check_desired_provider(
                config,
                desired_provider_fingerprint=desired_provider_fingerprint,
                desired_config_fingerprint=desired_config_fingerprint,
            )
        else:
            deploy_required = check_desired_provider(config)
        with terminal_status("Refreshing runtime users"):
            output = apply_once(config, force=True)
        console.print(output)
        if deploy_required:
            console.print("Pending configuration changes require `sftpwarden deploy`.")
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("plan")
def runtime_plan(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the container runtime synchronization plan.

    Parameters
    ----------
    config
        Runtime config path inside the container.
    json_output
        Whether to emit the plan as JSON.
    """
    try:
        loaded, users, state = load_runtime_inputs(config)
        sync_plan = build_runtime_plan(loaded, users, state)
        if json_output:
            print_json(runtime_plan_to_json(sync_plan))
            return
        console.print(
            runtime_plan_explanation(sync_plan, apply_command="sftpwarden runtime refresh")
        )
        console.print(sync_plan.summary())
        print_runtime_plan(sync_plan)
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("sync")
def runtime_sync(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
) -> None:
    """Run the long-lived runtime synchronization loop.

    Parameters
    ----------
    config
        Runtime config path inside the container.
    """
    try:
        console.print("[bold]Starting runtime sync loop[/bold]")
        run_sync_loop(config)
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("health")
def runtime_health(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
    json_output: Annotated[bool, typer.Option("--json")] = False,
    probe: Annotated[Probe, typer.Option("--probe")] = "readiness",
) -> None:
    """Check runtime-internal health without applying changes.

    Parameters
    ----------
    config
        Runtime config path inside the container.
    json_output
        Whether to emit JSON.
    probe
        Select readiness, liveness, or startup semantics.
    """
    try:
        report = runtime_health_report(config, probe=probe)
        if json_output:
            print_json(report.as_dict())
            raise typer.Exit(0 if report.healthy else 1)
        for check in report.checks:
            console.print(f"[bold]{check.name}[/bold]: {check.status} - {check.message}")
        raise typer.Exit(0 if report.healthy else 1)
    except SFTPWardenError as exc:
        handle_error(exc)
