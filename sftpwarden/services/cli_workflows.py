from __future__ import annotations

from rich.prompt import Confirm

from sftpwarden.contexts import ContextEntry
from sftpwarden.refresh import refresh_context
from sftpwarden.utils.console import console, print_info, terminal_status
from sftpwarden.watcher import ensure_watcher, installed_watcher_mode


def print_refresh_after_user_change(entry: ContextEntry) -> None:
    """Refresh a context and print the resulting command output.

    Parameters
    ----------
    entry
        Context affected by a user mutation.
    """
    with terminal_status(f"Refreshing context {entry.name}"):
        output = refresh_context(entry)
    console.print(output)


def install_context_watcher(
    entry: ContextEntry,
    *,
    requested_mode: str | None,
    yes: bool,
) -> None:
    """Install or reuse the watcher required by a context.

    Parameters
    ----------
    entry
        Context that may require a watcher.
    requested_mode
        Optional watcher mode requested by the caller.
    yes
        Whether confirmation prompts should be skipped.
    """
    if not entry.watcher_required:
        return
    existing = installed_watcher_mode()
    replace = False
    if existing and requested_mode and existing.value != requested_mode:
        replace = yes or Confirm.ask(
            f"Replace existing {existing.value} watcher with {requested_mode}?", default=False
        )
        if not replace:
            print_info(f"Using existing [bold]{existing.value}[/bold] watcher.")
            return
    result = ensure_watcher(requested_mode=requested_mode, yes=yes or replace)
    console.print(result)
