from __future__ import annotations

from rich.prompt import Confirm

from sftpwarden.contexts import ContextEntry
from sftpwarden.refresh import refresh_context
from sftpwarden.utils.console import console
from sftpwarden.watcher import ensure_watcher, installed_watcher_mode


def remote_url_from_parts(*, host: str, remote_root: str, remote_user: str | None) -> str:
    """Build a compact remote URL from CLI parts.

    Parameters
    ----------
    host
        Remote SSH host.
    remote_root
        Remote project root.
    remote_user
        Optional SSH user.

    Returns
    -------
    str
        URL in ``user@host:/path`` or ``host:/path`` form.
    """
    prefix = f"{remote_user}@" if remote_user else ""
    return f"{prefix}{host}:{remote_root}"


def print_refresh_after_user_change(entry: ContextEntry) -> None:
    """Refresh a context and print the resulting command output.

    Parameters
    ----------
    entry
        Context affected by a user mutation.
    """
    console.print(refresh_context(entry))


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
            console.print(f"Using existing [bold]{existing.value}[/bold] watcher.")
            return
    result = ensure_watcher(requested_mode=requested_mode, yes=yes or replace)
    console.print(result)
