from __future__ import annotations

from typing import Annotated

import typer
from rich.prompt import Confirm

from sftpwarden.cli_commands.app import watcher_app
from sftpwarden.cli_commands.output import (
    handle_error,
    print_json,
)
from sftpwarden.contexts import require_initialized_context
from sftpwarden.utils.console import console, print_success, terminal_status
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
    """Show watcher installation status.

    Parameters
    ----------
    json_output
        Whether to emit machine-readable JSON.
    """
    try:
        require_initialized_context()
        if json_output:
            print_json(watcher_status_data())
            return
        console.print(watcher_status_text())
    except SFTPWardenError as exc:
        handle_error(exc)


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
    """Install or update the local watcher.

    Parameters
    ----------
    watcher_mode
        Requested watcher mode.
    image
        Optional Docker image override for Docker watcher mode.
    activate
        Whether to start or enable the watcher after writing files.
    yes
        Whether confirmation prompts should be skipped.
    dry_run
        Whether to print planned commands without changing files.
    """
    try:
        require_initialized_context()
        existing = installed_watcher_mode()
        if existing and watcher_mode and existing.value != watcher_mode and not yes:
            if not Confirm.ask(
                f"Replace existing {existing.value} watcher with {watcher_mode}?",
                default=False,
            ):
                raise typer.Exit(1)
            yes = True
        if dry_run:
            console.print(
                install_watcher(
                    mode=watcher_mode,
                    yes=yes,
                    dry_run=True,
                    image=image,
                    activate=activate,
                )
            )
            return
        with terminal_status("Installing watcher"):
            result = install_watcher(
                mode=watcher_mode,
                yes=yes,
                dry_run=False,
                image=image,
                activate=activate,
            )
        print_success(result)
    except SFTPWardenError as exc:
        handle_error(exc)


@watcher_app.command("uninstall")
def watcher_uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Uninstall the local watcher.

    Parameters
    ----------
    yes
        Whether confirmation prompts should be skipped.
    dry_run
        Whether to print planned commands without changing files.
    """
    try:
        require_initialized_context()
        if not yes and not dry_run and not Confirm.ask("Uninstall watcher?", default=False):
            raise typer.Exit(1)
        if dry_run:
            console.print(uninstall_watcher(dry_run=True))
            return
        with terminal_status("Uninstalling watcher"):
            result = uninstall_watcher(dry_run=False)
        print_success(result)
    except SFTPWardenError as exc:
        handle_error(exc)
