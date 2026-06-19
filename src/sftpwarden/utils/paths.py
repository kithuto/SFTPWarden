from __future__ import annotations

import os
from pathlib import Path

from sftpwarden.utils.constants import CONFIG_FILENAME, DEFAULT_HOME


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def app_home() -> Path:
    return expand_path(os.environ.get("SFTPWARDEN_HOME", DEFAULT_HOME))


def global_config_path() -> Path:
    return app_home() / "config.toml"


def contexts_path() -> Path:
    return app_home() / "contexts.toml"


def project_config_path(root: str | Path) -> Path:
    return expand_path(root) / CONFIG_FILENAME


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
