from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path
from typing import TypedDict, cast

_DISTRIBUTION_NAME = "sftpwarden"


class _ProjectMetadata(TypedDict):
    """Subset of project metadata used to resolve the package version."""

    name: str
    version: str


class _PyprojectData(TypedDict):
    """Subset of ``pyproject.toml`` used by this module."""

    project: _ProjectMetadata


def _read_pyproject(pyproject: Path) -> _PyprojectData:
    """Read the project metadata needed for version resolution."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return cast(_PyprojectData, data)


def _is_distribution_pyproject(pyproject: Path) -> bool:
    """Return whether a pyproject belongs to this distribution."""
    try:
        project = _read_pyproject(pyproject)["project"]
    except (KeyError, tomllib.TOMLDecodeError):
        return False
    return project.get("name") == _DISTRIBUTION_NAME


def _pyproject_path() -> Path | None:
    """Find this distribution's nearest source-tree pyproject file."""
    for directory in Path(__file__).resolve().parents:
        candidate = directory / "pyproject.toml"
        if candidate.exists() and _is_distribution_pyproject(candidate):
            return candidate
    return None


def _read_pyproject_version(pyproject: Path) -> str:
    """Return the declared version from a pyproject file."""
    return _read_pyproject(pyproject)["project"]["version"]


def get_version() -> str:
    """Return the source-tree or installed distribution version."""
    pyproject = _pyproject_path()
    if pyproject is not None:
        return _read_pyproject_version(pyproject)

    return metadata.version(_DISTRIBUTION_NAME)
