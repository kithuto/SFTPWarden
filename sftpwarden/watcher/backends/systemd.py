from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from sftpwarden.utils.paths import app_home
from sftpwarden.utils.platform import current_username, executable_command, system_is
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.constants import SERVICE_NAME
from sftpwarden.watcher.registry import register_watcher


def systemd_unit_path() -> Path:
    """Return the generated systemd unit path."""
    return app_home() / "watcher" / "systemd" / f"{SERVICE_NAME}.service"


def render_systemd_unit() -> str:
    """Render the systemd watcher unit."""
    command = shlex.join([*executable_command("sftpwarden", env_fallback=True), "watch"])
    home = str(app_home()).replace("\\", "\\\\").replace('"', '\\"')
    user = current_username()
    return f"""[Unit]
Description=SFTPWarden remote local-sync watcher

[Service]
Type=simple
User={user}
Environment="SFTPWARDEN_HOME={home}"
ExecStart={command}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


@register_watcher
class SystemdWatcher(BaseWatcher):
    """Watcher backend managed by systemd."""

    mode = WatcherInstallMode.SYSTEMD
    auto_priority = 30

    @classmethod
    def is_supported(cls) -> bool:
        """Return whether systemd is available on the current host."""
        return (
            system_is("Linux")
            and shutil.which("systemctl") is not None
            and Path("/run/systemd/system").exists()
        )

    @classmethod
    def path(cls) -> Path:
        """Return the generated systemd unit path."""
        return systemd_unit_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        """Render the systemd watcher service unit."""
        return render_systemd_unit()

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        """Return commands that install and start the systemd watcher."""
        return [
            [
                "sudo",
                "install",
                "-m",
                "0644",
                str(systemd_unit_path()),
                f"/etc/systemd/system/{SERVICE_NAME}.service",
            ],
            ["sudo", "systemctl", "daemon-reload"],
            ["sudo", "systemctl", "enable", "--now", f"{SERVICE_NAME}.service"],
        ]

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        """Return commands that stop and remove the systemd watcher."""
        return [
            ["sudo", "systemctl", "disable", "--now", f"{SERVICE_NAME}.service"],
            ["sudo", "rm", "-f", f"/etc/systemd/system/{SERVICE_NAME}.service"],
            ["sudo", "systemctl", "daemon-reload"],
        ]
