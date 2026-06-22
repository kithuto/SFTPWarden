from __future__ import annotations

from urllib.parse import quote

from sftpwarden.config import ProviderType


def sql_dsn_scheme(provider: ProviderType) -> str:
    """Return the conventional database URL scheme for a SQL provider.

    Parameters
    ----------
    provider
        SQL provider type.

    Returns
    -------
    str
        URL scheme used in provider DSNs.
    """
    if provider == ProviderType.MYSQL:
        return "mysql"
    return "postgresql"


def sql_default_port(provider: ProviderType) -> int:
    """Return the default TCP port for a SQL provider.

    Parameters
    ----------
    provider
        SQL provider type.

    Returns
    -------
    int
        Default database port.
    """
    if provider == ProviderType.MYSQL:
        return 3306
    return 5432


def build_sql_dsn(
    *,
    scheme: str,
    username: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> str:
    """Build a SQL database URL with URL-escaped credentials.

    Parameters
    ----------
    scheme
        Database URL scheme.
    username
        Database username.
    password
        Database password.
    host
        Database host.
    port
        Database TCP port.
    database
        Database name.

    Returns
    -------
    str
        Database URL/DSN.
    """
    quoted_user = quote(username, safe="")
    quoted_password = quote(password, safe="")
    quoted_database = quote(database, safe="")
    credentials = quoted_user
    if password:
        credentials = f"{quoted_user}:{quoted_password}"
    return f"{scheme}://{credentials}@{host}:{port}/{quoted_database}"
