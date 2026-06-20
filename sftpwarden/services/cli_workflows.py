from __future__ import annotations

from rich.prompt import Confirm

from sftpwarden.contexts import ContextEntry
from sftpwarden.refresh import refresh_context
from sftpwarden.utils.console import console
from sftpwarden.watcher import ensure_watcher, installed_watcher_mode


def remote_url_from_parts(*, host: str, remote_root: str, remote_user: str | None) -> str:
    prefix = f"{remote_user}@" if remote_user else ""
    return f"{prefix}{host}:{remote_root}"


def print_refresh_after_user_change(entry: ContextEntry) -> None:
    console.print(refresh_context(entry))


def install_context_watcher(
    entry: ContextEntry,
    *,
    requested_mode: str | None,
    yes: bool,
) -> None:
    if not entry.watcher_required:
        return
    existing = installed_watcher_mode()
    replace = False
    if existing and requested_mode and existing.value != requested_mode:
        replace = yes or Confirm.ask(
            f"Replace existing {existing.value} watcher with {requested_mode}?", default=False
        )
        if not replace:
            console.print(f"Using existing [bold]{existing.value}[/bold] watcher.")
            return
    result = ensure_watcher(requested_mode=requested_mode, yes=yes or replace)
    console.print(result)
