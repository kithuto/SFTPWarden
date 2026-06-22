from __future__ import annotations

from typing import Annotated

import typer

from sftpwarden.cli_commands.app import runtime_app
from sftpwarden.cli_commands.output import (
    handle_error,
    print_json,
    print_runtime_plan,
    runtime_plan_explanation,
    runtime_plan_to_json,
)
from sftpwarden.runtime import (
    apply_once,
    build_runtime_plan,
    load_runtime_inputs,
    run_sync_loop,
)
from sftpwarden.utils.console import console, terminal_status
from sftpwarden.utils.errors import SFTPWardenError


@runtime_app.command("refresh")
def runtime_refresh(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
) -> None:
    """Force one runtime synchronization pass.

    Parameters
    ----------
    config
        Runtime config path inside the container.
    """
    try:
        with terminal_status("Refreshing runtime users"):
            output = apply_once(config, force=True)
        console.print(output)
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
