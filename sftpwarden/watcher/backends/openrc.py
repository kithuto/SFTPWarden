from __future__ import annotations

import shutil
from pathlib import Path

from sftpwarden.utils.paths import app_home
from sftpwarden.utils.platform import current_username, executable_path, system_is
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.constants import SERVICE_NAME
from sftpwarden.watcher.registry import register_watcher


def openrc_script_path() -> Path:
    """Return the generated OpenRC service path."""
    return app_home() / "watcher" / "openrc" / SERVICE_NAME


def render_openrc_script() -> str:
    """Render the OpenRC watcher service."""
    command = executable_path("sftpwarden")
    user = current_username()
    return f"""#!/sbin/openrc-run
name="{SERVICE_NAME}"
description="SFTPWarden remote local-sync watcher"
command="{command}"
command_args="watch"
command_user="{user}"
command_background=true
pidfile="/run/${{RC_SVCNAME}}.pid"
output_log="/var/log/{SERVICE_NAME}.log"
error_log="/var/log/{SERVICE_NAME}.log"
export SFTPWARDEN_HOME="{app_home()}"

depend() {{
    need net
}}
"""


@register_watcher
class OpenRCWatcher(BaseWatcher):
    """Watcher backend managed by OpenRC."""

    mode = WatcherInstallMode.OPENRC
    auto_priority = 40

    @classmethod
    def is_supported(cls) -> bool:
        """Return whether OpenRC is available on the current host."""
        return (
            system_is("Linux")
            and shutil.which("rc-service") is not None
            and shutil.which("rc-update") is not None
        )

    @classmethod
    def path(cls) -> Path:
        """Return the generated OpenRC service-script path."""
        return openrc_script_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        """Render the OpenRC watcher service script."""
        return render_openrc_script()

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        """Return commands that install and start the OpenRC watcher."""
        return [
            [
                "sudo",
                "install",
                "-m",
                "0755",
                str(openrc_script_path()),
                f"/etc/init.d/{SERVICE_NAME}",
            ],
            ["sudo", "rc-update", "add", SERVICE_NAME, "default"],
            ["sudo", "rc-service", SERVICE_NAME, "start"],
        ]

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        """Return commands that stop and remove the OpenRC watcher."""
        return [
            ["sudo", "rc-service", SERVICE_NAME, "stop"],
            ["sudo", "rc-update", "del", SERVICE_NAME, "default"],
            ["sudo", "rm", "-f", f"/etc/init.d/{SERVICE_NAME}"],
        ]
