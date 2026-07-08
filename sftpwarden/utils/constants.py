"""Shared constants for SFTPWarden paths, defaults, and config keys."""

from __future__ import annotations

APP_NAME = "sftpwarden"
CONFIG_FILENAME = "sftpwarden.yaml"
CONTAINER_CONFIG_PATH = "/etc/sftpwarden/sftpwarden.yaml"
CONTAINER_PROVIDER_DIR = "/etc/sftpwarden"
DEFAULT_GROUP = "sftpwarden_users"
DEFAULT_HOME = "~/.sftpwarden"
DEFAULT_LOCAL_ROOT = "~/sftpwarden"
DEFAULT_PROVIDER = "yaml"
DEFAULT_REMOTE_ROOT = "~/sftpwarden"
DEFAULT_SSH_PORT = 22
HOST_SSH_PORT = 2222
PRODUCTION_NAMES = {"prod", "production", "prd", "live", "main"}
PROJECT_CONFIG_PATHS = [
    "version",
    "project.name",
    "project.description",
    "server.host",
    "server.port",
    "server.data_dir",
    "server.host_keys_dir",
    "server.state_dir",
    "server.group",
    "sync.enabled",
    "sync.interval_seconds",
    "sync.apply_on_startup",
    "sync.disable_missing_users",
    "sync.delete_missing_user_data",
    "auth.allow_public_key",
    "auth.allow_password",
    "auth.recommended",
    "auth.password_hash_scheme",
    "isolation.mode",
    "isolation.upload_dir",
    "isolation.root_owner",
    "isolation.root_group",
    "isolation.root_permissions",
    "isolation.upload_permissions",
    "uid_gid.mode",
    "uid_gid.start",
    "uid_gid.end",
    "uid_gid.preserve_existing",
    "provider.type",
    "provider.path",
    "provider.dsn",
    "provider.query",
    "provider.table",
    "provider.collection",
    "provider.user_schema",
    "logging.level",
    "logging.format",
    "healthcheck.interval_seconds",
    "healthcheck.timeout_seconds",
    "healthcheck.retries",
    "healthcheck.start_period_seconds",
    "docker.image",
    "docker.container_name",
    "docker.restart",
    "docker.compose_file",
    "deploy.target",
    "kubernetes.mode",
    "kubernetes.namespace",
    "kubernetes.release",
    "kubernetes.kube_context",
    "kubernetes.service_type",
    "kubernetes.storage_class",
    "kubernetes.data_storage_size",
    "kubernetes.startup_probe.period_seconds",
    "kubernetes.startup_probe.timeout_seconds",
    "kubernetes.startup_probe.failure_threshold",
    "kubernetes.readiness_probe.period_seconds",
    "kubernetes.readiness_probe.timeout_seconds",
    "kubernetes.readiness_probe.failure_threshold",
    "kubernetes.liveness_probe.period_seconds",
    "kubernetes.liveness_probe.timeout_seconds",
    "kubernetes.liveness_probe.failure_threshold",
    "kubernetes.replicas",
    "remote.enabled",
    "remote.storage",
    "remote.host",
    "remote.user",
    "remote.port",
    "remote.remote_root",
    "remote.remote_config",
    "remote.ssh_key",
    "remote.delete_extra_files",
    "remote.include_env",
    "watcher.enabled",
    "watcher.mode",
    "watcher.image",
]
