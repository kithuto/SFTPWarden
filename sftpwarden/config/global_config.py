from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.utils.constants import (
    DEFAULT_LOCAL_ROOT,
    DEFAULT_PROVIDER,
    DEFAULT_REMOTE_ROOT,
    DEFAULT_SSH_PORT,
)
from sftpwarden.utils.errors import ConfigError
from sftpwarden.utils.paths import app_home, global_config_path
from sftpwarden.utils.persistence import atomic_bytes
from sftpwarden.utils.state_transactions import state_transaction


class DefaultsConfig(BaseModel):
    """Global default values used by CLI commands."""

    model_config = ConfigDict(extra="forbid")

    root: str = DEFAULT_LOCAL_ROOT
    remote_root: str = DEFAULT_REMOTE_ROOT
    ssh_port: int = DEFAULT_SSH_PORT
    remote_storage: str = "local-sync"
    watcher_mode: str = "auto"
    sync_interval_seconds: int = Field(default=60, ge=5)


class WatcherState(BaseModel):
    """Persisted global watcher installation state."""

    model_config = ConfigDict(extra="forbid")

    installed: bool = False
    mode: str | None = None
    managed_by: str = "sftpwarden"
    path: str | None = None
    activated: bool | None = None


class UpdateConfig(BaseModel):
    """Operator preferences for advisory release checks."""

    model_config = ConfigDict(extra="forbid")
    check_enabled: bool = True


class GlobalConfig(BaseModel):
    """Global SFTPWarden CLI configuration."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    default_provider: ProviderType | None = None
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    watcher: WatcherState = Field(default_factory=WatcherState)
    updates: UpdateConfig = Field(default_factory=UpdateConfig)


def load_global_config(path: Path | None = None) -> GlobalConfig:
    """Load global CLI configuration.

    Parameters
    ----------
    path
        Optional config path.

    Returns
    -------
    GlobalConfig
        Loaded config or defaults when missing.
    """
    config_path = path or global_config_path()
    if not config_path.exists():
        return GlobalConfig()
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        return GlobalConfig.model_validate(data)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigError(
            f"Invalid global config: {config_path}",
            suggestion=(
                "Fix the TOML file or remove it and run `sftpwarden config default-provider yaml`."
            ),
        ) from exc


def save_global_config(config: GlobalConfig, path: Path | None = None) -> None:
    """Save global CLI configuration.

    This is a full replacement. Read-modify-write callers must hold
    ``state_transaction`` from before loading through this save.

    Parameters
    ----------
    config
        Global config to save.
    path
        Optional config path.
    """
    config_path = path or global_config_path()
    with state_transaction(config_path):
        atomic_bytes(
            config_path,
            tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)).encode("utf-8"),
        )


def ensure_home() -> None:
    """Ensure the SFTPWarden app home exists."""
    app_home().mkdir(parents=True, exist_ok=True)


def resolve_provider(explicit: str | None = None) -> ProviderType:
    """Resolve the default provider type.

    Parameters
    ----------
    explicit
        Optional explicit provider value.

    Returns
    -------
    ProviderType
        Resolved provider type.
    """
    if explicit:
        return ProviderType(explicit)
    env_provider = os.environ.get("SFTPWARDEN_DEFAULT_PROVIDER")
    if env_provider:
        return ProviderType(env_provider)
    global_config = load_global_config()
    if global_config.default_provider:
        return global_config.default_provider
    return ProviderType(DEFAULT_PROVIDER)


def global_config_data() -> dict[str, Any]:
    """Return global config as JSON-serializable data.

    Returns
    -------
    dict[str, Any]
        Global config mapping.
    """
    return load_global_config().model_dump(mode="json", exclude_none=True)
