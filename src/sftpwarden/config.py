from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sftpwarden.constants import (
    CONFIG_FILENAME,
    CONTAINER_PROVIDER_DIR,
    DEFAULT_GROUP,
    HOST_SSH_PORT,
)
from sftpwarden.errors import ConfigError
from sftpwarden.paths import expand_path


class ProviderType(StrEnum):
    YAML = "yaml"
    CSV = "csv"
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"


class RemoteStorage(StrEnum):
    LOCAL_SYNC = "local-sync"
    REMOTE_ONLY = "remote-only"


class WatcherMode(StrEnum):
    SYSTEMD = "systemd"
    DOCKER = "docker"


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = "SFTPWarden environment"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=HOST_SSH_PORT, ge=1, le=65535)
    data_dir: str = "/data"
    host_keys_dir: str = "/etc/sftpwarden/host_keys"
    state_dir: str = "/var/lib/sftpwarden"
    group: str = DEFAULT_GROUP


class SyncConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=5)
    apply_on_startup: bool = True
    disable_missing_users: bool = True
    delete_missing_user_data: bool = False


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_public_key: bool = True
    allow_password: bool = True
    recommended: Literal["public_key", "password"] = "password"
    password_hash_scheme: Literal["yescrypt", "sha512crypt"] = "yescrypt"

    @model_validator(mode="after")
    def ensure_auth_method(self) -> AuthConfig:
        if not self.allow_public_key and not self.allow_password:
            raise ValueError("At least one authentication method must be enabled.")
        return self


class IsolationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["chroot"] = "chroot"
    upload_dir: str = "upload"
    root_owner: str = "root"
    root_group: str = "root"
    root_permissions: str = "755"
    upload_permissions: str = "750"


class UidGidConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto"] = "auto"
    start: int = Field(default=10000, ge=1000)
    end: int = Field(default=60000, ge=1001)
    preserve_existing: bool = True

    @model_validator(mode="after")
    def ensure_range(self) -> UidGidConfig:
        if self.end <= self.start:
            raise ValueError("uid_gid.end must be greater than uid_gid.start.")
        return self


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ProviderType = ProviderType.YAML
    path: str = f"{CONTAINER_PROVIDER_DIR}/users.yaml"
    dsn: str | None = None
    query: str | None = None

    @model_validator(mode="after")
    def validate_provider(self) -> ProviderConfig:
        if self.type in {ProviderType.MYSQL, ProviderType.POSTGRESQL}:
            if not self.dsn:
                raise ValueError(f"{self.type.value} provider requires dsn.")
        elif self.dsn or self.query:
            raise ValueError("dsn/query are only supported for SQL providers.")
        return self


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["debug", "info", "warning", "error"] = "info"
    format: Literal["json", "text"] = "json"


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = "sftpwarden:local"
    container_name: str = "sftpwarden"
    restart: str = "unless-stopped"
    compose_file: str = "docker-compose.yml"


class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    storage: RemoteStorage = RemoteStorage.LOCAL_SYNC
    host: str | None = None
    user: str | None = None
    port: int = Field(default=22, ge=1, le=65535)
    remote_root: str | None = None
    remote_config: str | None = None
    ssh_key: str | None = None
    delete_extra_files: bool = False
    include_env: bool = False


class WatcherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: WatcherMode = WatcherMode.SYSTEMD
    image: str | None = None

    @model_validator(mode="after")
    def validate_watcher(self) -> WatcherConfig:
        if self.mode == WatcherMode.SYSTEMD and self.image:
            raise ValueError("watcher.image is only valid when watcher.mode is docker.")
        if self.mode == WatcherMode.DOCKER and self.enabled and not self.image:
            self.image = "sftpwarden-watcher:local"
        return self


class SFTPWardenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    project: ProjectConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    isolation: IsolationConfig = Field(default_factory=IsolationConfig)
    uid_gid: UidGidConfig = Field(default_factory=UidGidConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)


def validation_error_to_config_error(
    error: ValidationError, source: Path | None = None
) -> ConfigError:
    details = []
    for item in error.errors():
        loc = ".".join(str(part) for part in item["loc"])
        details.append(f"{loc}: {item['msg']}")
    prefix = f"Invalid config in {source}: " if source else "Invalid config: "
    return ConfigError(
        prefix + "; ".join(details),
        suggestion="Run `sftpwarden init` or fix the YAML keys shown above.",
    )


def load_config(path: str | Path = CONFIG_FILENAME) -> SFTPWardenConfig:
    config_path = expand_path(path)
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}",
            suggestion="Run `sftpwarden init <name>` or pass --config with an existing file.",
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if isinstance(data, dict):
        validate_raw_config_keys(data)
    try:
        return SFTPWardenConfig.model_validate(data)
    except ValidationError as exc:
        raise validation_error_to_config_error(exc, config_path) from exc


def dump_config(config: SFTPWardenConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False)


def write_config(path: str | Path, config: SFTPWardenConfig) -> None:
    config_path = expand_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(dump_config(config), encoding="utf-8")


def default_project_config(
    name: str, provider: ProviderType = ProviderType.YAML
) -> SFTPWardenConfig:
    provider_path = f"{CONTAINER_PROVIDER_DIR}/users.yaml"
    if provider == ProviderType.CSV:
        provider_path = f"{CONTAINER_PROVIDER_DIR}/users.csv"
    return SFTPWardenConfig(
        project=ProjectConfig(name=name),
        provider=ProviderConfig(type=provider, path=provider_path),
    )


def provider_local_path(project_root: str | Path, config: SFTPWardenConfig) -> Path:
    root = expand_path(project_root)
    provider_path = Path(config.provider.path)
    if provider_path.is_absolute():
        return root / provider_path.name
    return root / provider_path


def config_as_json(config: SFTPWardenConfig) -> str:
    return json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True)


def validate_raw_config_keys(data: dict[str, Any]) -> None:
    if isinstance(data.get("server"), dict) and "container_port" in data["server"]:
        raise ConfigError(
            "server.container_port is not supported. The container SSH port is always 22.",
            suggestion="Use server.port to configure the host port exposed by Docker.",
        )
