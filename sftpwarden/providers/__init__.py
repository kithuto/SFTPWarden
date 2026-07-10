from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from sftpwarden.config import (
    ProviderConfig,
    ProviderType,
    SFTPWardenConfig,
    provider_local_path,
)
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.csv_provider import CSVProvider
from sftpwarden.providers.mongodb_provider import MongoDBProvider
from sftpwarden.providers.mysql_provider import (
    MariaDBProvider,
    MySQLProvider,
    mariadb_connect_kwargs,
    mysql_connect_kwargs,
)
from sftpwarden.providers.postgres_provider import PostgreSQLProvider
from sftpwarden.providers.registry import (
    build_provider,
    provider_class,
    provider_from_config,
    register_provider,
    registered_providers,
)
from sftpwarden.providers.sql import sql_select_users_query, users_from_sql_rows
from sftpwarden.providers.sqlite_provider import SQLiteProvider
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
    "MariaDBProvider",
    "MongoDBProvider",
    "MySQLProvider",
    "PostgreSQLProvider",
    "ProviderUsers",
    "SFTPUser",
    "SQLiteProvider",
    "YAMLProvider",
    "build_provider",
    "empty_provider_text",
    "find_user",
    "load_users",
    "load_users_from_project",
    "mariadb_connect_kwargs",
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


class _SchemaTextFactory(Protocol):
    """Callable shape exposed by schema-aware file providers."""

    def __call__(self, schema_version: int) -> str: ...


def empty_provider_text(provider_type: ProviderType, *, user_schema: int = 2) -> str:
    """Return empty provider text for a provider type.

    Parameters
    ----------
    provider_type
        Provider type.
    user_schema
        User schema version for providers with file seed content. Defaults to v2.

    Returns
    -------
    str
        Empty provider document.
    """
    provider = provider_class(provider_type)
    schema_factory = getattr(provider, "empty_text_for_schema", None)
    if schema_factory is not None:
        return cast(_SchemaTextFactory, schema_factory)(user_schema)
    return provider.empty_text()


def load_users_from_project(project_root: str | Path, config: SFTPWardenConfig) -> ProviderUsers:
    """Load provider users for a project.

    Parameters
    ----------
    project_root
        Project root.
    config
        Project config.

    Returns
    -------
    ProviderUsers
        Loaded provider users.
    """
    return provider_from_config(project_root, config).read()


def load_users(
    provider_type: ProviderType,
    path: str | Path,
    *,
    dsn: str | None = None,
    query: str | None = None,
    table: str = "sftp_users",
    collection: str = "sftp_users",
    user_schema: int = 2,
) -> ProviderUsers:
    """Load users from an explicit provider.

    Parameters
    ----------
    provider_type
        Provider type.
    path
        Provider path.
    dsn
        Optional SQL DSN.
    query
        Optional SQL read query.
    table
        SQL table name.
    collection
        MongoDB collection name.
    user_schema
        Preferred user schema for providers that need initialization context.

    Returns
    -------
    ProviderUsers
        Loaded provider users.
    """
    return build_provider(
        provider_type,
        path=path,
        dsn=dsn,
        query=query,
        table=table,
        collection=collection,
        user_schema=user_schema,
    ).read()


def save_users(
    provider_type: ProviderType,
    path: str | Path,
    users: ProviderUsers,
    *,
    dsn: str | None = None,
    table: str = "sftp_users",
    collection: str = "sftp_users",
    user_schema: int | None = None,
) -> None:
    """Save users to an explicit provider.

    Parameters
    ----------
    provider_type
        Provider type.
    path
        Provider path.
    users
        Users to persist.
    dsn
        Optional SQL DSN.
    table
        SQL table name.
    collection
        MongoDB collection name.
    user_schema
        Optional schema version to mark before writing.
    """
    if user_schema is not None:
        users = ProviderUsers(schema_version=user_schema, users=users.users)
    build_provider(
        provider_type,
        path=path,
        dsn=dsn,
        table=table,
        collection=collection,
        user_schema=users.schema_version,
    ).write(users)


def provider_for_project(project_root: str | Path, config: SFTPWardenConfig) -> BaseProvider:
    """Return a provider for a project.

    Parameters
    ----------
    project_root
        Project root.
    config
        Project config.

    Returns
    -------
    BaseProvider
        Provider instance.
    """
    return provider_from_config(project_root, config)


def provider_for_config(
    provider_config: ProviderConfig, path: str | Path | None = None
) -> BaseProvider:
    """Return a provider from a provider config.

    Parameters
    ----------
    provider_config
        Provider config.
    path
        Optional provider file path.

    Returns
    -------
    BaseProvider
        Provider instance.
    """
    provider_path = Path(path) if path is not None else None
    return provider_class(provider_config.type)(provider_config, path=provider_path)


def provider_local(project_root: str | Path, config: SFTPWardenConfig) -> BaseProvider:
    """Return the local provider for a project config.

    Parameters
    ----------
    project_root
        Project root.
    config
        Project config.

    Returns
    -------
    BaseProvider
        Provider instance.
    """
    return provider_class(config.provider.type)(
        config.provider,
        path=provider_local_path(project_root, config),
    )
