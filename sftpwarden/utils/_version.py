from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

_DISTRIBUTION_NAME = "sftpwarden"


def _read_pyproject(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))


def _is_distribution_pyproject(pyproject: Path) -> bool:
    try:
        project = _read_pyproject(pyproject)["project"]
    except (KeyError, tomllib.TOMLDecodeError):
        return False
    return project.get("name") == _DISTRIBUTION_NAME


def _pyproject_path() -> Path | None:
    for directory in Path(__file__).resolve().parents:
        candidate = directory / "pyproject.toml"
        if candidate.exists() and _is_distribution_pyproject(candidate):
            return candidate
    return None


def _read_pyproject_version(pyproject: Path) -> str:
    return _read_pyproject(pyproject)["project"]["version"]


def get_version() -> str:
    pyproject = _pyproject_path()
    if pyproject is not None:
        return _read_pyproject_version(pyproject)

    return metadata.version(_DISTRIBUTION_NAME)
