from __future__ import annotations

import os

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.providers.sql import (
    create_sql_users_table_statement,
    delete_missing_sql_users,
    delete_sql_user,
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


@register_provider
class PostgreSQLProvider(BaseProvider):
    """PostgreSQL-backed user provider."""

    provider_type = ProviderType.POSTGRESQL

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
        """Read users from PostgreSQL.

        Returns
        -------
        ProviderUsers
            Users loaded from PostgreSQL.

        Raises
        ------
        ProviderError
            Raised when DSN or optional dependencies are missing.
        """
        dsn = self.config.dsn
        if not dsn:
            raise ProviderError("PostgreSQL provider requires dsn.")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ProviderError(
                "PostgreSQL provider requires the postgres optional dependency.",
                suggestion='Install SFTPWarden with the "postgres" extra.',
            ) from exc
        with (
            psycopg.connect(
                os.path.expandvars(dsn),
                row_factory=dict_row,  # type: ignore
            ) as connection,
            connection.cursor() as cursor,
        ):
            query = self.config.query or sql_select_users_query(self.config.table)
            if self.config.query:
                validate_sql_read_query(query)
            cursor.execute(query)  # type: ignore
            return users_from_sql_rows(cursor.fetchall())  # type: ignore

    def write(self, users: ProviderUsers) -> None:
        """Replace the PostgreSQL users table with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider mutations require dsn.")
        validate_sql_table(self.config.table)
        try:
            import psycopg
        except ImportError as exc:
            raise ProviderError(
                "PostgreSQL provider requires the postgres optional dependency.",
                suggestion='Install SFTPWarden with the "postgres" extra.',
            ) from exc
        with (
            psycopg.connect(os.path.expandvars(self.config.dsn)) as connection,
            connection.cursor() as cursor,
        ):
            upsert_sql_users(cursor, self.config.table, users, dialect="postgres")
            delete_missing_sql_users(cursor, self.config.table, users)

    def upsert_user(self, user: SFTPUser) -> None:
        """Create or update one PostgreSQL user row.

        Parameters
        ----------
        user
            User to persist.
        """
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider mutations require dsn.")
        validate_sql_table(self.config.table)
        try:
            import psycopg
        except ImportError as exc:
            raise ProviderError(
                "PostgreSQL provider requires the postgres optional dependency.",
                suggestion='Install SFTPWarden with the "postgres" extra.',
            ) from exc
        with (
            psycopg.connect(os.path.expandvars(self.config.dsn)) as connection,
            connection.cursor() as cursor,
        ):
            upsert_sql_user(cursor, self.config.table, user, dialect="postgres")

    def remove_user(self, username: str) -> None:
        """Remove one PostgreSQL user row.

        Parameters
        ----------
        username
            Username to remove.
        """
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider mutations require dsn.")
        validate_sql_table(self.config.table)
        try:
            import psycopg
        except ImportError as exc:
            raise ProviderError(
                "PostgreSQL provider requires the postgres optional dependency.",
                suggestion='Install SFTPWarden with the "postgres" extra.',
            ) from exc
        with (
            psycopg.connect(os.path.expandvars(self.config.dsn)) as connection,
            connection.cursor() as cursor,
        ):
            delete_sql_user(cursor, self.config.table, username)

    def table_exists(self) -> bool:
        """Return whether the configured PostgreSQL users table exists.

        Returns
        -------
        bool
            ``True`` when the table can be queried.
        """
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider requires dsn.")
        validate_sql_table(self.config.table)
        try:
            import psycopg
        except ImportError as exc:
            raise ProviderError(
                "PostgreSQL provider requires the postgres optional dependency.",
                suggestion='Install SFTPWarden with the "postgres" extra.',
            ) from exc
        try:
            with (
                psycopg.connect(os.path.expandvars(self.config.dsn)) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(sql_check_table_query(self.config.table))
                return True
        except Exception as exc:
            sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
            if sqlstate == "42P01":
                return False
            raise

    def create_table(self) -> None:
        """Create the configured PostgreSQL users table."""
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider requires dsn.")
        validate_sql_table(self.config.table)
        try:
            import psycopg
        except ImportError as exc:
            raise ProviderError(
                "PostgreSQL provider requires the postgres optional dependency.",
                suggestion='Install SFTPWarden with the "postgres" extra.',
            ) from exc
        with (
            psycopg.connect(os.path.expandvars(self.config.dsn)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(create_sql_users_table_statement(self.config.table))
