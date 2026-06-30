from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class WatcherInstallMode(StrEnum):
    """Supported watcher installation modes."""

    AUTO = "auto"
    SYSTEMD = "systemd"
    OPENRC = "openrc"
    RUNIT = "runit"
    SUPERVISORD = "supervisord"
    LAUNCHD = "launchd"
    WINDOWS_TASK = "windows-task"
    DOCKER = "docker"


@dataclass(frozen=True)
class WatcherInstallPlan:
    """Files and commands needed to install the watcher."""

    mode: WatcherInstallMode
    path: Path
    commands: list[list[str]]

    def text(self) -> str:
        """Render the watcher install plan for dry-run output.

        Returns
        -------
        str
            Human-readable install plan.
        """
        rendered = [f"{self.mode.value} watcher: {self.path}"]
        rendered.extend(" ".join(command) for command in self.commands)
        return "\n".join(rendered)


@dataclass(frozen=True)
class WatcherUninstallPlan:
    """Commands needed to deactivate and remove a watcher backend."""

    mode: WatcherInstallMode
    path: Path | None
    commands: list[list[str]]

    def text(self) -> str:
        """Render the watcher uninstall plan for dry-run output."""
        rendered = [f"{self.mode.value} watcher uninstall: {self.path or ''}"]
        rendered.extend(" ".join(command) for command in self.commands)
        return "\n".join(rendered)


@dataclass(frozen=True)
class WatcherImageReference:
    """Resolved Docker watcher image and optional local build metadata."""

    image: str
    build: dict[str, str] | None = None
    pull_before_up: bool = False

    @property
    def local_build(self) -> bool:
        """Return whether Docker Compose should build the watcher image locally."""
        return self.build is not None


class BaseWatcher(ABC):
    """Base class for watcher installation backends."""

    mode: WatcherInstallMode
    auto_priority: int = 100
    native_scheduler: bool = True
    accepts_image: bool = False

    @classmethod
    @abstractmethod
    def is_supported(cls) -> bool:
        """Return whether this backend is available on the current host."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def path(cls) -> Path:
        """Return the generated local file path for this backend."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def render(cls, *, image: str | None = None) -> str:
        """Render backend-specific watcher configuration."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        """Return commands needed to activate this backend."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        """Return commands that would deactivate this backend."""
        raise NotImplementedError

    @classmethod
    def plan(cls, *, image: str | None = None) -> WatcherInstallPlan:
        """Build a watcher install plan for this backend."""
        return WatcherInstallPlan(
            mode=cls.mode,
            path=cls.path(),
            commands=cls.commands(image=image),
        )

    @classmethod
    def uninstall_plan(cls, *, path: Path | None = None) -> WatcherUninstallPlan:
        """Build a watcher uninstall plan for this backend."""
        return WatcherUninstallPlan(
            mode=cls.mode,
            path=path or cls.path(),
            commands=cls.uninstall_commands(path=path),
        )

    @classmethod
    def write(cls, *, image: str | None = None) -> Path:
        """Write backend-specific watcher files and return their path."""
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cls.render(image=image), encoding="utf-8")
        return path
