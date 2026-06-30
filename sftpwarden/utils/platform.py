from __future__ import annotations

import os
import platform
import shutil


def system_is(name: str) -> bool:
    """Return whether the current platform matches ``name``."""
    return platform.system().lower() == name.lower()


def executable_path(name: str, *, fallback: str | None = None) -> str:
    """Return an executable path from PATH or a stable fallback name."""
    return shutil.which(name) or fallback or name


def executable_command(name: str, *, env_fallback: bool = False) -> list[str]:
    """Return command parts for running an executable by name.

    Parameters
    ----------
    name
        Executable name to resolve.
    env_fallback
        Whether to fall back to ``/usr/bin/env name`` when PATH lookup fails.
    """
    resolved = shutil.which(name)
    if resolved:
        return [resolved]
    if env_fallback:
        return ["/usr/bin/env", name]
    return [name]


def current_username(*, default: str = "sftpwarden") -> str:
    """Return the user that host service managers should run as."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user
    return (
        os.environ.get("USER") or os.environ.get("LOGNAME") or os.environ.get("USERNAME") or default
    )
