from __future__ import annotations

from typing import Annotated

import typer

from sftpwarden.cli_commands.common import (
    handle_error,
    print_json,
    print_runtime_plan,
    runtime_app,
    runtime_plan_to_json,
)
from sftpwarden.runtime import (
    apply_once,
    build_runtime_plan,
    load_runtime_inputs,
    run_sync_loop,
)
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError


@runtime_app.command("refresh")
def runtime_refresh(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
) -> None:
    try:
        console.print(apply_once(config, force=True))
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("plan")
def runtime_plan(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        loaded, users, state = load_runtime_inputs(config)
        sync_plan = build_runtime_plan(loaded, users, state)
        if json_output:
            print_json(runtime_plan_to_json(sync_plan))
            return
        console.print(sync_plan.summary())
        print_runtime_plan(sync_plan)
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("sync")
def runtime_sync(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
) -> None:
    try:
        run_sync_loop(config)
    except SFTPWardenError as exc:
        handle_error(exc)
