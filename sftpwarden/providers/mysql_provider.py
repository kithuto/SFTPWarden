from __future__ import annotations

import os
from typing import Any, ClassVar
from urllib.parse import unquote, urlparse

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.providers.sql import (
    create_sql_users_table_statement,
    delete_missing_sql_users,
    delete_sql_user,
    execute_validated_sql,
    sql_check_table_query,
    sql_select_users_query,
    upsert_sql_user,
    upsert_sql_users,
    users_from_sql_rows,
    validate_sql_read_query,
    validate_sql_table,
)
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


class PyMySQLProvider(BaseProvider):
    """Shared PyMySQL-backed provider implementation."""

    provider_label: ClassVar[str] = "MySQL"
    allowed_schemes: ClassVar[set[str]] = {"mysql", "mysql+pymysql"}
    default_port: ClassVar[int] = 3306

    @classmethod
    def empty_text(cls) -> str:
        """Return an empty document for SQL providers.

        Returns
        -------
        str
            Empty string because SQL providers do not use seed files.
        """
        return ""

    def read(self) -> ProviderUsers:
        """Read users from a PyMySQL-compatible database.

        Returns
        -------
        ProviderUsers
            Users loaded from the database.
        """
        connection = self._connect(dict_cursor=True)
        try:
            with connection.cursor() as cursor:
                query = self.config.query or sql_select_users_query(self.config.table)
                if self.config.query:
                    validate_sql_read_query(query)
                execute_validated_sql(cursor, query)
                return users_from_sql_rows(list(cursor.fetchall()))
        finally:
            connection.close()

    def write(self, users: ProviderUsers) -> None:
        """Replace the users table with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                upsert_sql_users(cursor, self.config.table, users, dialect="mysql")
                delete_missing_sql_users(cursor, self.config.table, users)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def upsert_user(self, user: SFTPUser) -> None:
        """Create or update one database user row.

        Parameters
        ----------
        user
            User to persist.
        """
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                upsert_sql_user(cursor, self.config.table, user, dialect="mysql")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def remove_user(self, username: str) -> None:
        """Remove one database user row.

        Parameters
        ----------
        username
            Username to remove.
        """
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                delete_sql_user(cursor, self.config.table, username)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_exists(self) -> bool:
        """Return whether the configured users table exists.

        Returns
        -------
        bool
            ``True`` when the table can be queried.
        """
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                execute_validated_sql(cursor, sql_check_table_query(self.config.table))
                return True
        except Exception as exc:
            if getattr(exc, "args", [None])[0] == 1146:
                return False
            raise
        finally:
            connection.close()

    def create_table(self) -> None:
        """Create the configured users table."""
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                execute_validated_sql(cursor, create_sql_users_table_statement(self.config.table))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self, *, dict_cursor: bool = False) -> Any:
        if not self.config.dsn:
            raise ProviderError(f"{self.provider_label} provider requires dsn.")
        try:
            import pymysql
        except ImportError as exc:
            raise ProviderError(
                (
                    f"{self.provider_label} provider requires the mysql optional dependency; "
                    "the mariadb extra is an equivalent alias."
                ),
                suggestion='Install SFTPWarden with "sftpwarden[mysql]" or "sftpwarden[mariadb]".',
            ) from exc
        kwargs = pymysql_connect_kwargs(
            os.path.expandvars(self.config.dsn),
            allowed_schemes=self.allowed_schemes,
            default_port=self.default_port,
            provider_label=self.provider_label,
        )
        if dict_cursor:
            kwargs["cursorclass"] = pymysql.cursors.DictCursor
        return pymysql.connect(**kwargs)


@register_provider
class MySQLProvider(PyMySQLProvider):
    """MySQL-backed user provider."""

    provider_type = ProviderType.MYSQL
    provider_label: ClassVar[str] = "MySQL"
    allowed_schemes: ClassVar[set[str]] = {"mysql", "mysql+pymysql"}


@register_provider
class MariaDBProvider(PyMySQLProvider):
    """MariaDB-backed user provider using the PyMySQL-compatible implementation."""

    provider_type = ProviderType.MARIADB
    provider_label: ClassVar[str] = "MariaDB"
    allowed_schemes: ClassVar[set[str]] = {"mariadb", "mariadb+pymysql"}


def pymysql_connect_kwargs(
    dsn: str,
    *,
    allowed_schemes: set[str],
    default_port: int,
    provider_label: str,
) -> dict[str, Any]:
    """Parse a PyMySQL-compatible DSN into connection keyword arguments.

    Parameters
    ----------
    dsn
        Database connection URL.
    allowed_schemes
        Accepted URL schemes.
    default_port
        Default TCP port.
    provider_label
        User-facing provider label.

    Returns
    -------
    dict[str, Any]
        Connection keyword arguments for PyMySQL.
    """
    parsed = urlparse(dsn)
    if parsed.scheme not in allowed_schemes:
        schemes = " or ".join(f"{scheme}://" for scheme in sorted(allowed_schemes))
        raise ProviderError(f"{provider_label} DSN must use {schemes}.")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or default_port,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
    }


def mysql_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Parse a MySQL DSN into PyMySQL keyword arguments.

    Parameters
    ----------
    dsn
        MySQL connection URL.

    Returns
    -------
    dict[str, Any]
        Connection keyword arguments for PyMySQL.
    """
    return pymysql_connect_kwargs(
        dsn,
        allowed_schemes=MySQLProvider.allowed_schemes,
        default_port=MySQLProvider.default_port,
        provider_label=MySQLProvider.provider_label,
    )


def mariadb_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Parse a MariaDB DSN into PyMySQL keyword arguments.

    Parameters
    ----------
    dsn
        MariaDB connection URL.

    Returns
    -------
    dict[str, Any]
        Connection keyword arguments for PyMySQL.
    """
    return pymysql_connect_kwargs(
        dsn,
        allowed_schemes=MariaDBProvider.allowed_schemes,
        default_port=MariaDBProvider.default_port,
        provider_label=MariaDBProvider.provider_label,
    )
