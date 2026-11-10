from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from sftpwarden.utils.paths import app_home
from sftpwarden.utils.platform import current_username, executable_command, system_is
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.constants import SERVICE_NAME
from sftpwarden.watcher.registry import register_watcher


def supervisor_config_target() -> str:
    """Return the supervisor program config target path."""
    candidates = [
        Path("/etc/supervisor/conf.d"),
        Path("/etc/supervisord.d"),
        Path("/usr/local/etc/supervisord.d"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate / f"{SERVICE_NAME}.conf")
    return f"/etc/supervisor/conf.d/{SERVICE_NAME}.conf"


def supervisord_config_path() -> Path:
    """Return the generated supervisord config path."""
    return app_home() / "watcher" / "supervisord" / f"{SERVICE_NAME}.conf"


def render_supervisord_config() -> str:
    """Render the supervisord watcher program config."""
    command = shlex.join([*executable_command("sftpwarden", env_fallback=True), "watch"])
    return f"""[program:{SERVICE_NAME}]
command={command}
user={current_username()}
environment=SFTPWARDEN_HOME="{app_home()}"
autostart=true
autorestart=true
startsecs=5
stdout_logfile=/var/log/{SERVICE_NAME}.log
stderr_logfile=/var/log/{SERVICE_NAME}.err.log
"""


@register_watcher
class SupervisordWatcher(BaseWatcher):
    """Watcher backend managed by supervisord."""

    mode = WatcherInstallMode.SUPERVISORD
    auto_priority = 60

    @classmethod
    def is_supported(cls) -> bool:
        """Return whether supervisord is available on the current host."""
        return (
            system_is("Linux")
            and shutil.which("supervisord") is not None
            and shutil.which("supervisorctl") is not None
        )

    @classmethod
    def path(cls) -> Path:
        """Return the generated supervisord configuration path."""
        return supervisord_config_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        """Render the supervisord watcher configuration."""
        return render_supervisord_config()

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        """Return commands that install and start the supervisord watcher."""
        return [
            [
                "sudo",
                "install",
                "-m",
                "0644",
                str(supervisord_config_path()),
                supervisor_config_target(),
            ],
            ["sudo", "supervisorctl", "reread"],
            ["sudo", "supervisorctl", "update"],
            ["sudo", "supervisorctl", "start", SERVICE_NAME],
        ]

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        """Return commands that stop and remove the supervisord watcher."""
        return [
            ["sudo", "supervisorctl", "stop", SERVICE_NAME],
            ["sudo", "rm", "-f", supervisor_config_target()],
            ["sudo", "supervisorctl", "reread"],
            ["sudo", "supervisorctl", "update"],
        ]
