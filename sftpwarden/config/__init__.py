"""Project configuration models, validation, and persistence helpers."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from sftpwarden.users.schemas import validate_user_schema_version
from sftpwarden.utils.constants import (
    CONFIG_FILENAME,
    CONTAINER_PROVIDER_DIR,
    DEFAULT_GROUP,
    HOST_SSH_PORT,
)
from sftpwarden.utils.errors import ConfigError, ProviderError
from sftpwarden.utils.files import write_private_text
from sftpwarden.utils.paths import expand_path
from sftpwarden.utils.validation import (
    validate_octal_permissions,
    validate_provider_path,
    validate_relative_safe_path,
)

SQL_TABLE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$", flags=re.ASCII)
KUBERNETES_STORAGE_QUANTITY_RE = re.compile(
    r"^(?=.*[1-9])(?:0|[1-9]\d*)(?:\.\d+)?(?:Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?$"
)


class ProviderType(StrEnum):
    """Supported user provider types."""

    YAML = "yaml"
    CSV = "csv"
    SQLITE = "sqlite"
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    MARIADB = "mariadb"
    MONGODB = "mongodb"


FILE_PROVIDER_TYPES = {ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE}
RELATIONAL_PROVIDER_TYPES = {
    ProviderType.SQLITE,
    ProviderType.MYSQL,
    ProviderType.POSTGRESQL,
    ProviderType.MARIADB,
}
EXTERNAL_DSN_PROVIDER_TYPES = {
    ProviderType.MYSQL,
    ProviderType.POSTGRESQL,
    ProviderType.MARIADB,
    ProviderType.MONGODB,
}
SQL_QUERY_PROVIDER_TYPES = {
    ProviderType.MYSQL,
    ProviderType.POSTGRESQL,
    ProviderType.MARIADB,
}
WATCHER_SYNC_PROVIDER_TYPES = FILE_PROVIDER_TYPES


def _is_container_provider_path(path: PurePosixPath) -> bool:
    """Return whether a POSIX provider path points inside the runtime config dir."""
    container_root = PurePosixPath(CONTAINER_PROVIDER_DIR)
    return path == container_root or container_root in path.parents


class RemoteStorage(StrEnum):
    """Supported storage modes for remote contexts."""

    LOCAL_SYNC = "local-sync"
    REMOTE_ONLY = "remote-only"


class WatcherMode(StrEnum):
    """Supported watcher installation modes."""

    AUTO = "auto"
    SYSTEMD = "systemd"
    OPENRC = "openrc"
    RUNIT = "runit"
    SUPERVISORD = "supervisord"
    LAUNCHD = "launchd"
    WINDOWS_TASK = "windows-task"
    DOCKER = "docker"


class DeployTarget(StrEnum):
    """Supported deployment targets."""

    COMPOSE = "compose"
    KUBERNETES = "kubernetes"


class KubernetesMode(StrEnum):
    """Supported Kubernetes deployment modes."""

    MANIFESTS = "manifests"
    HELM = "helm"


class KubernetesServiceType(StrEnum):
    """Supported Kubernetes Service types."""

    CLUSTER_IP = "ClusterIP"
    NODE_PORT = "NodePort"
    LOAD_BALANCER = "LoadBalancer"


KUBERNETES_REPLICAS_ERROR = (
    "Kubernetes replicas > 1 are not supported yet.\n\n"
    "Reason:\n"
    "SFTPWarden currently runs one OpenSSH runtime per context. Multi-pod runtime "
    "requires shared storage, shared host keys, provider-safe refresh and UID/GID "
    "consistency.\n\n"
    "Use replicas: 1 for now."
)


class ProjectConfig(BaseModel):
    """Project metadata configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = "SFTPWarden environment"


class ServerConfig(BaseModel):
    """OpenSSH runtime server configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=HOST_SSH_PORT, ge=1, le=65535)
    data_dir: str = "/data"
    host_keys_dir: str = "/etc/sftpwarden/host_keys"
    state_dir: str = "/var/lib/sftpwarden"
    group: str = DEFAULT_GROUP


class SyncConfig(BaseModel):
    """Runtime synchronization behavior."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=5)
    apply_on_startup: bool = True
    disable_missing_users: bool = True
    delete_missing_user_data: bool = False


class AuthConfig(BaseModel):
    """Authentication behavior for SFTP users."""

    model_config = ConfigDict(extra="forbid")

    allow_public_key: bool = True
    allow_password: bool = True
    recommended: Literal["public_key", "password"] = "password"
    password_hash_scheme: Literal["sha512crypt"] = "sha512crypt"

    @model_validator(mode="after")
    def ensure_auth_method(self) -> AuthConfig:
        """Ensure at least one login method is enabled.

        Returns
        -------
        AuthConfig
            Validated authentication config.
        """
        if not self.allow_public_key and not self.allow_password:
            raise ValueError("At least one authentication method must be enabled.")
        return self


class IsolationConfig(BaseModel):
    """Chroot and upload directory isolation settings."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["chroot"] = "chroot"
    upload_dir: str = "upload"
    root_owner: str = "root"
    root_group: str = "root"
    root_permissions: str = "755"
    upload_permissions: str = "750"

    @field_validator("upload_dir")
    @classmethod
    def validate_upload_dir(cls, value: str) -> str:
        """Validate the configured upload directory.

        Parameters
        ----------
        value
            Upload directory from config.

        Returns
        -------
        str
            Validated relative path.
        """
        validate_relative_safe_path(value, field_name="isolation.upload_dir")
        return value

    @field_validator("root_permissions")
    @classmethod
    def validate_root_permissions(cls, value: str) -> str:
        """Validate chroot root permissions.

        Parameters
        ----------
        value
            Octal permission string.

        Returns
        -------
        str
            Validated permission string.
        """
        validate_octal_permissions(value, field_name="isolation.root_permissions")
        if int(value, 8) & 0o022:
            raise ValueError("isolation.root_permissions must not be writable by group or others.")
        return value

    @field_validator("upload_permissions")
    @classmethod
    def validate_upload_permissions(cls, value: str) -> str:
        """Validate upload directory permissions.

        Parameters
        ----------
        value
            Octal permission string.

        Returns
        -------
        str
            Validated permission string.
        """
        validate_octal_permissions(value, field_name="isolation.upload_permissions")
        if int(value, 8) & 0o002:
            raise ValueError("isolation.upload_permissions must not be world-writable.")
        return value


class UidGidConfig(BaseModel):
    """UID/GID allocation settings."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto"] = "auto"
    start: int = Field(default=10000, ge=1000)
    end: int = Field(default=60000, ge=1001)
    preserve_existing: bool = True

    @model_validator(mode="after")
    def ensure_range(self) -> UidGidConfig:
        """Ensure UID/GID allocation bounds are ordered.

        Returns
        -------
        UidGidConfig
            Validated allocation config.
        """
        if self.end <= self.start:
            raise ValueError("uid_gid.end must be greater than uid_gid.start.")
        return self


class ProviderConfig(BaseModel):
    """User provider configuration."""

    model_config = ConfigDict(extra="forbid")

    type: ProviderType = ProviderType.YAML
    path: str = f"{CONTAINER_PROVIDER_DIR}/users.yaml"
    dsn: str | None = None
    query: str | None = None
    table: str = "sftp_users"
    collection: str = "sftp_users"
    user_schema: int = Field(default=1, ge=1)

    @field_validator("user_schema")
    @classmethod
    def validate_user_schema(cls, value: int) -> int:
        """Validate the configured provider user schema version."""
        try:
            return validate_user_schema_version(value)
        except ProviderError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Validate provider path safety rules.

        Parameters
        ----------
        value
            Provider path from config.

        Returns
        -------
        str
            Validated provider path.
        """
        validate_provider_path(value)
        return value

    @field_validator("table")
    @classmethod
    def validate_table(cls, value: str) -> str:
        """Validate an SQL provider table name.

        Parameters
        ----------
        value
            Table name from config.

        Returns
        -------
        str
            Validated table name.
        """
        if not SQL_TABLE_RE.fullmatch(value):
            raise ValueError("provider.table must be a table name or schema-qualified table name.")
        return value

    @model_validator(mode="after")
    def validate_provider(self) -> ProviderConfig:
        """Validate provider-specific fields.

        Returns
        -------
        ProviderConfig
            Validated provider config.
        """
        if self.type in SQL_QUERY_PROVIDER_TYPES:
            if not self.dsn:
                raise ValueError(f"{self.type.value} provider requires dsn.")
            if self.query:
                from sftpwarden.providers.sql import validate_sql_read_query

                try:
                    validate_sql_read_query(self.query)
                except ProviderError as exc:
                    raise ValueError(str(exc)) from exc
            if self.collection != "sftp_users":
                raise ValueError("provider.collection is only supported for mongodb providers.")
        elif self.type == ProviderType.MONGODB:
            if not self.dsn:
                raise ValueError("mongodb provider requires dsn.")
            if self.query or self.table != "sftp_users":
                raise ValueError("provider.query/table are not supported for mongodb providers.")
        elif self.type == ProviderType.SQLITE:
            if self.dsn or self.query or self.collection != "sftp_users":
                raise ValueError(
                    "provider.dsn/query/collection are not supported for sqlite providers."
                )
        elif (
            self.dsn or self.query or self.table != "sftp_users" or self.collection != "sftp_users"
        ):
            raise ValueError(
                "provider.dsn/query/table/collection are only supported for SQL/database providers."
            )
        return self


class LoggingConfig(BaseModel):
    """Runtime logging configuration."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["debug", "info", "warning", "error"] = "info"
    format: Literal["json", "text"] = "json"


class HealthcheckConfig(BaseModel):
    """Docker Compose runtime healthcheck timing configuration."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(default=30, ge=1)
    timeout_seconds: int = Field(default=10, ge=1)
    retries: int = Field(default=3, ge=1)
    start_period_seconds: int = Field(default=20, ge=0)


class DockerConfig(BaseModel):
    """Docker Compose rendering configuration."""

    model_config = ConfigDict(extra="forbid")

    image: str = "sftpwarden:local"
    container_name: str = "sftpwarden"
    restart: str = "unless-stopped"
    compose_file: str = "docker-compose.yml"


class DeployConfig(BaseModel):
    """Deployment target selection."""

    model_config = ConfigDict(extra="forbid")

    target: DeployTarget = DeployTarget.COMPOSE


class KubernetesProbeConfig(BaseModel):
    """Kubernetes runtime probe timing configuration."""

    model_config = ConfigDict(extra="forbid")

    period_seconds: int = Field(default=10, ge=1)
    timeout_seconds: int = Field(default=5, ge=1)
    failure_threshold: int = Field(default=3, ge=1)


class KubernetesStartupProbeConfig(KubernetesProbeConfig):
    """Kubernetes startup probe timing configuration."""

    period_seconds: int = Field(default=5, ge=1)
    timeout_seconds: int = Field(default=5, ge=1)
    failure_threshold: int = Field(default=30, ge=1)


class KubernetesReadinessProbeConfig(KubernetesProbeConfig):
    """Kubernetes readiness probe timing configuration."""

    period_seconds: int = Field(default=10, ge=1)
    timeout_seconds: int = Field(default=5, ge=1)
    failure_threshold: int = Field(default=3, ge=1)


class KubernetesLivenessProbeConfig(KubernetesProbeConfig):
    """Kubernetes liveness probe timing configuration."""

    period_seconds: int = Field(default=30, ge=1)
    timeout_seconds: int = Field(default=5, ge=1)
    failure_threshold: int = Field(default=3, ge=1)


class KubernetesConfig(BaseModel):
    """Kubernetes deployment configuration."""

    model_config = ConfigDict(extra="forbid")

    mode: KubernetesMode = KubernetesMode.MANIFESTS
    namespace: str = Field(default="sftpwarden", min_length=1)
    release: str = Field(default="sftpwarden", min_length=1)
    kube_context: str | None = None
    service_type: KubernetesServiceType = KubernetesServiceType.CLUSTER_IP
    storage_class: str | None = None
    data_storage_size: str = "10Gi"
    startup_probe: KubernetesStartupProbeConfig = Field(
        default_factory=KubernetesStartupProbeConfig
    )
    readiness_probe: KubernetesReadinessProbeConfig = Field(
        default_factory=KubernetesReadinessProbeConfig
    )
    liveness_probe: KubernetesLivenessProbeConfig = Field(
        default_factory=KubernetesLivenessProbeConfig
    )
    replicas: int = Field(default=1, ge=1)

    def ensure_supported_replicas(self) -> None:
        """Reject multi-pod runtime deployments until the runtime supports them."""
        if self.replicas > 1:
            raise ValueError(KUBERNETES_REPLICAS_ERROR)

    @field_validator("data_storage_size")
    @classmethod
    def validate_data_storage_size(cls, value: str) -> str:
        """Validate the user data PVC storage request.

        Parameters
        ----------
        value
            Kubernetes storage quantity for the SFTP data PVC.

        Returns
        -------
        str
            Validated Kubernetes storage quantity.
        """
        normalized = value.strip()
        if not KUBERNETES_STORAGE_QUANTITY_RE.fullmatch(normalized):
            raise ValueError(
                "kubernetes.data_storage_size must be a positive Kubernetes storage "
                "quantity such as 10Gi, 50Gi, or 500Mi."
            )
        return normalized

    @model_validator(mode="after")
    def validate_replicas(self) -> KubernetesConfig:
        """Reject multi-pod runtime deployments until the runtime supports them.

        Returns
        -------
        KubernetesConfig
            Validated Kubernetes config.
        """
        self.ensure_supported_replicas()
        return self


class RemoteConfig(BaseModel):
    """Remote deployment defaults."""

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
    """Watcher configuration stored in project config."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: WatcherMode = WatcherMode.AUTO
    image: str | None = None

    @model_validator(mode="after")
    def validate_watcher(self) -> WatcherConfig:
        """Validate watcher mode-specific fields.

        Returns
        -------
        WatcherConfig
            Validated watcher config.
        """
        if self.mode != WatcherMode.DOCKER and self.image:
            raise ValueError("watcher.image is only valid when watcher.mode is docker.")
        return self


class SFTPWardenConfig(BaseModel):
    """Root SFTPWarden project configuration."""

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
    healthcheck: HealthcheckConfig = Field(default_factory=HealthcheckConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)
    kubernetes: KubernetesConfig = Field(default_factory=KubernetesConfig)
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)


def validation_error_to_config_error(
    error: ValidationError, source: Path | None = None
) -> ConfigError:
    """Convert a Pydantic validation error into a user-facing config error.

    Parameters
    ----------
    error
        Pydantic validation error.
    source
        Optional source file path.

    Returns
    -------
    ConfigError
        Formatted SFTPWarden config error.
    """
    details = []
    replicas_messages = []
    for item in error.errors():
        loc = ".".join(str(part) for part in item["loc"])
        message = str(item["msg"])
        if "Kubernetes replicas > 1 are not supported yet." in message:
            replicas_messages.append(message.removeprefix("Value error, "))
        details.append(f"{loc}: {message}")
    prefix = f"Invalid config in {source}: " if source else "Invalid config: "
    if replicas_messages:
        return ConfigError(
            prefix + replicas_messages[0],
            suggestion="Set kubernetes.replicas to 1.",
        )
    return ConfigError(
        prefix + "; ".join(details),
        suggestion="Run `sftpwarden init` or fix the YAML keys shown above.",
    )


def load_config(path: str | Path = CONFIG_FILENAME) -> SFTPWardenConfig:
    """Load and validate a project configuration file.

    Parameters
    ----------
    path
        Config file path.

    Returns
    -------
    SFTPWardenConfig
        Validated project config.

    Raises
    ------
    ConfigError
        Raised when the file is missing or invalid.
    """
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
    """Serialize a project configuration to YAML.

    Parameters
    ----------
    config
        Project config to serialize.

    Returns
    -------
    str
        YAML config text.
    """
    data = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False)


def write_config(path: str | Path, config: SFTPWardenConfig) -> None:
    """Write a private project configuration file.

    Parameters
    ----------
    path
        Destination path.
    config
        Project config to write.
    """
    config_path = expand_path(path)
    write_private_text(config_path, dump_config(config))


def default_project_config(
    name: str,
    provider: ProviderType = ProviderType.YAML,
    *,
    dsn: str | None = None,
    query: str | None = None,
    table: str = "sftp_users",
    collection: str = "sftp_users",
    user_schema: int = 2,
    deploy_target: DeployTarget = DeployTarget.COMPOSE,
    kubernetes_mode: KubernetesMode = KubernetesMode.MANIFESTS,
) -> SFTPWardenConfig:
    """Create a default project configuration.

    Parameters
    ----------
    name
        Project name.
    provider
        Initial provider type.
    dsn
        Optional SQL provider DSN.
    query
        Optional SQL read query.
    table
        SQL users table name.
    collection
        MongoDB users collection name.
    user_schema
        Provider user schema version to initialize.
    deploy_target
        Initial deployment target for generated projects.
    kubernetes_mode
        Kubernetes deployment mode when the target is Kubernetes.

    Returns
    -------
    SFTPWardenConfig
        Default project config.
    """
    provider_path = f"{CONTAINER_PROVIDER_DIR}/users.yaml"
    if provider == ProviderType.CSV:
        provider_path = f"{CONTAINER_PROVIDER_DIR}/users.csv"
    if provider == ProviderType.SQLITE:
        provider_path = f"{CONTAINER_PROVIDER_DIR}/users.sqlite"
    deploy_config = DeployConfig(target=deploy_target)
    kubernetes_config = KubernetesConfig(mode=kubernetes_mode, release=name)
    if provider in SQL_QUERY_PROVIDER_TYPES:
        return SFTPWardenConfig(
            project=ProjectConfig(name=name),
            deploy=deploy_config,
            kubernetes=kubernetes_config,
            provider=ProviderConfig(
                type=provider,
                path=provider_path,
                dsn=dsn,
                query=query,
                table=table,
                user_schema=user_schema,
            ),
        )
    if provider == ProviderType.MONGODB:
        return SFTPWardenConfig(
            project=ProjectConfig(name=name),
            deploy=deploy_config,
            kubernetes=kubernetes_config,
            provider=ProviderConfig(
                type=provider,
                path=provider_path,
                dsn=dsn,
                collection=collection,
                user_schema=user_schema,
            ),
        )
    return SFTPWardenConfig(
        project=ProjectConfig(name=name),
        deploy=deploy_config,
        kubernetes=kubernetes_config,
        provider=ProviderConfig(type=provider, path=provider_path, user_schema=user_schema),
    )


def provider_local_path(project_root: str | Path, config: SFTPWardenConfig) -> Path:
    """Resolve the local provider file path for a project.

    Parameters
    ----------
    project_root
        Local project root.
    config
        Project config.

    Returns
    -------
    Path
        Local provider file path.
    """
    root = expand_path(project_root)
    raw_path = str(config.provider.path)
    container_path = PurePosixPath(raw_path)
    if container_path.is_absolute() and _is_container_provider_path(container_path):
        validate_provider_path(container_path.name)
        return root / container_path.name
    provider_path = Path(raw_path)
    if provider_path.is_absolute() or container_path.is_absolute():
        validate_provider_path(provider_path.name)
        return provider_path
    validate_relative_safe_path(str(provider_path), field_name="provider.path")
    return root / provider_path


def config_as_json(config: SFTPWardenConfig) -> str:
    """Serialize a project configuration as formatted JSON.

    Parameters
    ----------
    config
        Project config.

    Returns
    -------
    str
        Formatted JSON string.
    """
    return json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True)


def validate_raw_config_keys(data: dict[str, Any]) -> None:
    """Validate deprecated or unsupported raw config keys.

    Parameters
    ----------
    data
        Raw YAML mapping.

    Raises
    ------
    ConfigError
        Raised when unsupported keys are present.
    """
    if isinstance(data.get("server"), dict) and "container_port" in data["server"]:
        raise ConfigError(
            "server.container_port is not supported. The container SSH port is always 22.",
            suggestion="Use server.port to configure the host port exposed by Docker.",
        )
