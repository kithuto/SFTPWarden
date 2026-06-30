from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from sftpwarden.utils.paths import app_home
from sftpwarden.utils.platform import current_username, executable_command, system_is
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.constants import SERVICE_NAME
from sftpwarden.watcher.registry import register_watcher

RUNIT_SERVICE_DIR = f"/etc/sv/{SERVICE_NAME}"
RUNIT_ACTIVE_DIR = f"/var/service/{SERVICE_NAME}"


def runit_script_path() -> Path:
    """Return the generated runit script path."""
    return app_home() / "watcher" / "runit" / "run"


def render_runit_script() -> str:
    """Render the runit watcher run script."""
    command = shlex.join([*executable_command("sftpwarden", env_fallback=True), "watch"])
    user = shlex.quote(current_username())
    return f"""#!/bin/sh
export SFTPWARDEN_HOME={shlex.quote(str(app_home()))}
exec chpst -u {user} {command}
"""


@register_watcher
class RunitWatcher(BaseWatcher):
    """Watcher backend managed by runit."""

    mode = WatcherInstallMode.RUNIT
    auto_priority = 50

    @classmethod
    def is_supported(cls) -> bool:
        return (
            system_is("Linux")
            and shutil.which("sv") is not None
            and shutil.which("chpst") is not None
            and (Path("/etc/sv").exists() or Path("/var/service").exists())
        )

    @classmethod
    def path(cls) -> Path:
        return runit_script_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        return render_runit_script()

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        return [
            ["sudo", "mkdir", "-p", RUNIT_SERVICE_DIR, "/var/service"],
            [
                "sudo",
                "install",
                "-m",
                "0755",
                str(runit_script_path()),
                f"{RUNIT_SERVICE_DIR}/run",
            ],
            ["sudo", "ln", "-sfn", RUNIT_SERVICE_DIR, RUNIT_ACTIVE_DIR],
            ["sudo", "sv", "up", RUNIT_ACTIVE_DIR],
        ]

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        return [
            ["sudo", "sv", "down", RUNIT_ACTIVE_DIR],
            ["sudo", "rm", "-f", RUNIT_ACTIVE_DIR],
            ["sudo", "rm", "-rf", RUNIT_SERVICE_DIR],
        ]
