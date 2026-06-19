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
