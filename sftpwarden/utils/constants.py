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
    "logging.level",
    "logging.format",
    "docker.image",
    "docker.container_name",
    "docker.restart",
    "docker.compose_file",
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
