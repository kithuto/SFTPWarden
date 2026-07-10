from __future__ import annotations

import shutil
from pathlib import Path

from sftpwarden.utils.paths import app_home
from sftpwarden.utils.platform import executable_path, system_is
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.registry import register_watcher

WINDOWS_TASK_NAME = "SFTPWarden Watcher"


def windows_script_path() -> Path:
    """Return the generated Windows watcher script path."""
    return app_home() / "watcher" / "windows" / "sftpwarden-watch.ps1"


def render_windows_script() -> str:
    """Render the Windows Task Scheduler PowerShell wrapper."""
    executable = executable_path("sftpwarden").replace("'", "''")
    home = str(app_home()).replace("'", "''")
    return f"""$env:SFTPWARDEN_HOME = '{home}'
& '{executable}' watch
exit $LASTEXITCODE
"""


@register_watcher
class WindowsTaskWatcher(BaseWatcher):
    """Watcher backend managed by Windows Task Scheduler."""

    mode = WatcherInstallMode.WINDOWS_TASK
    auto_priority = 10

    @classmethod
    def is_supported(cls) -> bool:
        """Return whether Windows Task Scheduler is available."""
        return system_is("Windows") and (
            shutil.which("schtasks") is not None or shutil.which("schtasks.exe") is not None
        )

    @classmethod
    def path(cls) -> Path:
        """Return the generated PowerShell watcher-script path."""
        return windows_script_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        """Render the PowerShell watcher script."""
        return render_windows_script()

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        """Return commands that register and start the scheduled task."""
        task_command = (
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{windows_script_path()}"'
        )
        return [
            [
                "schtasks",
                "/Create",
                "/TN",
                WINDOWS_TASK_NAME,
                "/TR",
                task_command,
                "/SC",
                "ONLOGON",
                "/F",
            ],
            ["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME],
        ]

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        """Return the command that removes the scheduled task."""
        return [["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"]]
