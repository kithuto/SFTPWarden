from __future__ import annotations

import os
from pathlib import Path

from sftpwarden.utils.constants import CONFIG_FILENAME, DEFAULT_HOME


def expand_path(value: str | Path) -> Path:
    """Expand environment variables and ``~`` in a filesystem path.

    Parameters
    ----------
    value
        Raw path value.

    Returns
    -------
    Path
        Expanded path.
    """
    return Path(os.path.expandvars(str(value))).expanduser()


def app_home() -> Path:
    """Return the SFTPWarden application home directory.

    Returns
    -------
    Path
        Directory used for global config, contexts, and watcher metadata.
    """
    return expand_path(os.environ.get("SFTPWARDEN_HOME", DEFAULT_HOME))


def global_config_path() -> Path:
    """Return the global configuration file path.

    Returns
    -------
    Path
        Path to ``config.toml`` inside the app home.
    """
    return app_home() / "config.toml"


def contexts_path() -> Path:
    """Return the context registry path.

    Returns
    -------
    Path
        Path to ``contexts.toml`` inside the app home.
    """
    return app_home() / "contexts.toml"


def project_config_path(root: str | Path) -> Path:
    """Return the project configuration path for a root directory.

    Parameters
    ----------
    root
        Local project root.

    Returns
    -------
    Path
        Path to the project ``sftpwarden.yaml`` file.
    """
    return expand_path(root) / CONFIG_FILENAME


def ensure_parent(path: Path) -> None:
    """Create the parent directory for a path.

    Parameters
    ----------
    path
        File path whose parent should exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
