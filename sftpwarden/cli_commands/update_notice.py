"""Best-effort release notices for successful interactive management commands."""

from __future__ import annotations

import os

from packaging.version import Version

from sftpwarden.config.global_config import load_global_config
from sftpwarden.services.releases import discover_release
from sftpwarden.utils._version import get_version
from sftpwarden.utils.console import console


def notify_update(*, json_output: bool = False) -> None:
    """Never change command results, machine output or runtime behavior."""
    if (
        json_output
        or not console.is_terminal
        or os.environ.get("SFTPWARDEN_NO_UPDATE_CHECK") == "1"
    ):
        return
    try:
        if not load_global_config().updates.check_enabled:
            return
        result = discover_release()
        if result["status"] == "available" and Version(result["version"]) > Version(get_version()):
            console.print(
                f"SFTPWarden {result['version']} is available. Run `sftpwarden upgrade` "
                "to update the CLI and watcher; servers still require an explicit `deploy`."
            )
    except Exception:  # noqa: BLE001
        return
