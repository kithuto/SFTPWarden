from __future__ import annotations

from pathlib import Path

from sftpwarden.config import ProviderConfig, ProviderType, provider_local_path
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.csv_provider import CSVProvider
from sftpwarden.providers.mysql_provider import MySQLProvider, mysql_connect_kwargs
from sftpwarden.providers.postgres_provider import PostgreSQLProvider
from sftpwarden.providers.registry import (
    build_provider,
    provider_class,
    provider_from_config,
    register_provider,
    registered_providers,
)
from sftpwarden.providers.sql import sql_select_users_query, users_from_sql_rows
from sftpwarden.providers.yaml_provider import YAMLProvider
from sftpwarden.users import (
    ProviderUsers,
    SFTPUser,
    find_user,
    remove_user,
    upsert_user,
    users_fingerprint,
)

__all__ = [
    "BaseProvider",
    "CSVProvider",
    "MySQLProvider",
    "PostgreSQLProvider",
    "ProviderUsers",
    "SFTPUser",
    "YAMLProvider",
    "build_provider",
    "empty_provider_text",
    "find_user",
    "load_users",
    "load_users_from_project",
    "mysql_connect_kwargs",
    "provider_class",
    "provider_from_config",
    "register_provider",
    "registered_providers",
    "remove_user",
    "save_users",
    "sql_select_users_query",
    "upsert_user",
    "users_fingerprint",
    "users_from_sql_rows",
]


def empty_provider_text(provider_type: ProviderType) -> str:
    return provider_class(provider_type).empty_text()


def load_users_from_project(project_root: str | Path, config) -> ProviderUsers:
    return provider_from_config(project_root, config).read()


def load_users(
    provider_type: ProviderType,
    path: str | Path,
    *,
    dsn: str | None = None,
    query: str | None = None,
    table: str = "sftp_users",
) -> ProviderUsers:
    return build_provider(provider_type, path=path, dsn=dsn, query=query, table=table).read()


def save_users(
    provider_type: ProviderType,
    path: str | Path,
    users: ProviderUsers,
    *,
    dsn: str | None = None,
    table: str = "sftp_users",
) -> None:
    build_provider(provider_type, path=path, dsn=dsn, table=table).write(users)


def provider_for_project(project_root: str | Path, config) -> BaseProvider:
    return provider_from_config(project_root, config)


def provider_for_config(
    provider_config: ProviderConfig, path: str | Path | None = None
) -> BaseProvider:
    provider_path = Path(path) if path is not None else None
    return provider_class(provider_config.type)(provider_config, path=provider_path)


def provider_local(project_root: str | Path, config) -> BaseProvider:
    return provider_class(config.provider.type)(
        config.provider,
        path=provider_local_path(project_root, config),
    )
