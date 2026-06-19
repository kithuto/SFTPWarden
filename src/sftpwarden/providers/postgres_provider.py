from __future__ import annotations

import os

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.providers.sql import (
    delete_missing_sql_users,
    delete_sql_user,
    sql_select_users_query,
    upsert_sql_user,
    upsert_sql_users,
    users_from_sql_rows,
    validate_sql_table,
)
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


@register_provider
class PostgreSQLProvider(BaseProvider):
    provider_type = ProviderType.POSTGRESQL

    @classmethod
    def empty_text(cls) -> str:
        return ""

    def read(self) -> ProviderUsers:
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
                os.path.expandvars(dsn), row_factory=dict_row # type: ignore
            ) as connection,
            connection.cursor() as cursor,
        ):
            query = self.config.query or sql_select_users_query(self.config.table)
            cursor.execute(query) # type: ignore
            return users_from_sql_rows(cursor.fetchall()) # type: ignore

    def write(self, users: ProviderUsers) -> None:
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
