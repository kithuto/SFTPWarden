from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote, urlparse

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
    validate_sql_read_query,
    validate_sql_table,
)
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


@register_provider
class MySQLProvider(BaseProvider):
    provider_type = ProviderType.MYSQL

    @classmethod
    def empty_text(cls) -> str:
        return ""

    def read(self) -> ProviderUsers:
        if not self.config.dsn:
            raise ProviderError("MySQL provider requires dsn.")
        try:
            import pymysql
        except ImportError as exc:
            raise ProviderError(
                "MySQL provider requires the mysql optional dependency.",
                suggestion='Install SFTPWarden with the "mysql" extra.',
            ) from exc
        connection = pymysql.connect(
            **mysql_connect_kwargs(os.path.expandvars(self.config.dsn)),
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with connection.cursor() as cursor:
                query = self.config.query or sql_select_users_query(self.config.table)
                if self.config.query:
                    validate_sql_read_query(query)
                cursor.execute(query)
                return users_from_sql_rows(list(cursor.fetchall()))
        finally:
            connection.close()

    def write(self, users: ProviderUsers) -> None:
        if not self.config.dsn:
            raise ProviderError("MySQL provider mutations require dsn.")
        validate_sql_table(self.config.table)
        try:
            import pymysql
        except ImportError as exc:
            raise ProviderError(
                "MySQL provider requires the mysql optional dependency.",
                suggestion='Install SFTPWarden with the "mysql" extra.',
            ) from exc
        connection = pymysql.connect(**mysql_connect_kwargs(os.path.expandvars(self.config.dsn)))
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
        if not self.config.dsn:
            raise ProviderError("MySQL provider mutations require dsn.")
        validate_sql_table(self.config.table)
        try:
            import pymysql
        except ImportError as exc:
            raise ProviderError(
                "MySQL provider requires the mysql optional dependency.",
                suggestion='Install SFTPWarden with the "mysql" extra.',
            ) from exc
        connection = pymysql.connect(**mysql_connect_kwargs(os.path.expandvars(self.config.dsn)))
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
        if not self.config.dsn:
            raise ProviderError("MySQL provider mutations require dsn.")
        validate_sql_table(self.config.table)
        try:
            import pymysql
        except ImportError as exc:
            raise ProviderError(
                "MySQL provider requires the mysql optional dependency.",
                suggestion='Install SFTPWarden with the "mysql" extra.',
            ) from exc
        connection = pymysql.connect(**mysql_connect_kwargs(os.path.expandvars(self.config.dsn)))
        try:
            with connection.cursor() as cursor:
                delete_sql_user(cursor, self.config.table, username)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def mysql_connect_kwargs(dsn: str) -> dict[str, Any]:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ProviderError("MySQL DSN must use mysql:// or mysql+pymysql://.")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
    }
