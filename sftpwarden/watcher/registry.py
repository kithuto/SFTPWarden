from __future__ import annotations

from importlib import import_module
from typing import TypeVar

from sftpwarden.utils.errors import ContextError
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode

_WatcherT = TypeVar("_WatcherT", bound=BaseWatcher)
WatcherClass = type[BaseWatcher]
_WATCHERS: dict[WatcherInstallMode, WatcherClass] = {}
_BUILTINS_IMPORTED = False


def register_watcher(watcher_class: type[_WatcherT]) -> type[_WatcherT]:
    """Register a watcher backend class.

    Parameters
    ----------
    watcher_class
        Watcher backend class to register.

    Returns
    -------
    type[_WatcherT]
        The same class, enabling decorator usage.
    """
    _WATCHERS[watcher_class.mode] = watcher_class
    return watcher_class


def ensure_builtin_watchers() -> None:
    """Import built-in watcher backends once."""
    global _BUILTINS_IMPORTED
    if _BUILTINS_IMPORTED:
        return
    import_module("sftpwarden.watcher.backends")
    _BUILTINS_IMPORTED = True


def watcher_class(mode: WatcherInstallMode | str) -> WatcherClass:
    """Return the registered watcher backend class for a mode.

    Parameters
    ----------
    mode
        Watcher mode.

    Returns
    -------
    WatcherClass
        Registered watcher backend class.
    """
    ensure_builtin_watchers()
    normalized = WatcherInstallMode(mode)
    try:
        return _WATCHERS[normalized]
    except KeyError as exc:
        raise ContextError(f"Watcher backend is not registered: {normalized.value}") from exc


def registered_watchers() -> dict[WatcherInstallMode, WatcherClass]:
    """Return registered watcher backend classes."""
    ensure_builtin_watchers()
    return dict(_WATCHERS)
