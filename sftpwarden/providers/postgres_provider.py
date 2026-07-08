from __future__ import annotations

import os

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.providers.sql import (
    create_sql_user_keys_table_if_missing_statement,
    create_sql_user_keys_table_statement,
    create_sql_users_table_statement,
    delete_missing_sql_users,
    delete_sql_user,
    execute_validated_sql,
    schema_uses_key_table,
    sql_check_table_query,
    sql_select_user_keys_query,
    sql_select_users_query,
    sql_user_keys_table,
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
            execute_validated_sql(cursor, query)
            rows = cursor.fetchall()
            key_rows = None
            if schema_uses_key_table(self.config.user_schema):
                execute_validated_sql(cursor, sql_select_user_keys_query(self.config.table))
                key_rows = cursor.fetchall()
            return users_from_sql_rows(  # type: ignore
                rows,
                key_rows=key_rows,
                schema_version=self.config.user_schema,
            )

    def write(self, users: ProviderUsers) -> None:
        """Replace the PostgreSQL users table with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider mutations require dsn.")
        self.ensure_schema_storage(users.schema_version)
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
            upsert_sql_users(
                cursor,
                self.config.table,
                users,
                dialect="postgres",
                schema_version=users.schema_version,
            )
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
        self.ensure_schema_storage(self.config.user_schema)
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
            upsert_sql_user(
                cursor,
                self.config.table,
                user,
                dialect="postgres",
                schema_version=self.config.user_schema,
            )

    def remove_user(self, username: str) -> None:
        """Remove one PostgreSQL user row.

        Parameters
        ----------
        username
            Username to remove.
        """
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider mutations require dsn.")
        self.ensure_schema_storage(self.config.user_schema)
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
            delete_sql_user(
                cursor,
                self.config.table,
                username,
                schema_version=self.config.user_schema,
            )

    def table_exists(self) -> bool:
        """Return whether the configured PostgreSQL provider storage exists.

        Returns
        -------
        bool
            ``True`` when all tables required by the active schema can be queried.
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
                execute_validated_sql(cursor, sql_check_table_query(self.config.table))
                if schema_uses_key_table(self.config.user_schema):
                    execute_validated_sql(
                        cursor,
                        sql_check_table_query(sql_user_keys_table(self.config.table)),
                    )
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
            execute_validated_sql(cursor, create_sql_users_table_statement(self.config.table))
            if schema_uses_key_table(self.config.user_schema):
                execute_validated_sql(
                    cursor,
                    create_sql_user_keys_table_statement(self.config.table),
                )

    def ensure_schema_storage(self, schema_version: int) -> None:
        """Ensure tables required by a user schema exist."""
        if not self.config.dsn:
            raise ProviderError("PostgreSQL provider requires dsn.")
        validate_sql_table(self.config.table)
        if not schema_uses_key_table(schema_version):
            return
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
            execute_validated_sql(
                cursor,
                create_sql_user_keys_table_if_missing_statement(self.config.table),
            )
