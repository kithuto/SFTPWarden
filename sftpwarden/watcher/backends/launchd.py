from __future__ import annotations

import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from sftpwarden.utils.paths import app_home, expand_path
from sftpwarden.utils.platform import executable_path, system_is
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.registry import register_watcher

LAUNCHD_LABEL = "com.sftpwarden.watch"


def launchd_plist_path() -> Path:
    """Return the generated launchd plist path."""
    return app_home() / "watcher" / "launchd" / f"{LAUNCHD_LABEL}.plist"


def launchd_target_path() -> str:
    """Return the user LaunchAgents target path."""
    return str(expand_path("~/Library/LaunchAgents") / f"{LAUNCHD_LABEL}.plist")


def render_launchd_plist() -> str:
    """Render the macOS launchd watcher plist."""
    executable = escape(executable_path("sftpwarden"))
    home = escape(str(app_home()))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{executable}</string>
    <string>watch</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SFTPWARDEN_HOME</key>
    <string>{home}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
"""


@register_watcher
class LaunchdWatcher(BaseWatcher):
    """Watcher backend managed by macOS launchd."""

    mode = WatcherInstallMode.LAUNCHD
    auto_priority = 20

    @classmethod
    def is_supported(cls) -> bool:
        return system_is("Darwin") and shutil.which("launchctl") is not None

    @classmethod
    def path(cls) -> Path:
        return launchd_plist_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        return render_launchd_plist()

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        target = launchd_target_path()
        return [
            ["mkdir", "-p", str(Path(target).parent)],
            ["cp", str(launchd_plist_path()), target],
            ["launchctl", "load", "-w", target],
        ]

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        target = launchd_target_path()
        return [
            ["launchctl", "unload", "-w", target],
            ["rm", "-f", target],
        ]
