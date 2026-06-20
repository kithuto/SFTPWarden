from __future__ import annotations

from typing import Annotated

import typer
from rich.prompt import Confirm

from sftpwarden.cli_commands.common import (
    handle_error,
    print_json,
    watcher_app,
)
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.watcher import (
    install_watcher,
    installed_watcher_mode,
    uninstall_watcher,
    watcher_status_data,
    watcher_status_text,
)


@watcher_app.command("status")
def watcher_status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    if json_output:
        print_json(watcher_status_data())
        return
    console.print(watcher_status_text())


@watcher_app.command("install")
def watcher_install(
    watcher_mode: Annotated[
        str | None, typer.Option("--watcher", "--mode", help="Watcher mode.")
    ] = None,
    image: Annotated[str | None, typer.Option("--image", help="Docker watcher image.")] = None,
    activate: Annotated[
        bool,
        typer.Option(
            "--activate/--no-activate",
            help="Start/enable the watcher after writing its files.",
        ),
    ] = True,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    try:
        existing = installed_watcher_mode()
        if existing and watcher_mode and existing.value != watcher_mode and not yes:
            if not Confirm.ask(
                f"Replace existing {existing.value} watcher with {watcher_mode}?",
                default=False,
            ):
                raise typer.Exit(1)
            yes = True
        console.print(
            install_watcher(
                mode=watcher_mode,
                yes=yes,
                dry_run=dry_run,
                image=image,
                activate=activate,
            )
        )
    except SFTPWardenError as exc:
        handle_error(exc)


@watcher_app.command("uninstall")
def watcher_uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    try:
        if not yes and not dry_run and not Confirm.ask("Uninstall watcher?", default=False):
            raise typer.Exit(1)
        console.print(uninstall_watcher(dry_run=dry_run))
    except SFTPWardenError as exc:
        handle_error(exc)
