from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import FileProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.providers.sql import (
    DEFAULT_SQL_USERS_TABLE,
    SQL_USER_COLUMNS,
    sql_select_users_query,
    sql_user_row,
    users_from_sql_rows,
    validate_sql_table,
)
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import chmod_private


@register_provider
class SQLiteProvider(FileProvider):
    """SQLite-backed user provider."""

    provider_type = ProviderType.SQLITE

    @classmethod
    def empty_text(cls) -> str:
        """Return an empty document placeholder for SQLite providers.

        Returns
        -------
        str
            Empty string because SQLite providers use a database file.
        """
        return ""

    def read(self) -> ProviderUsers:
        """Read users from SQLite.

        Returns
        -------
        ProviderUsers
            Users loaded from SQLite.
        """
        path = self.ensure_exists()
        validate_sql_table(self.config.table)
        with closing(self._connect(path)) as connection:
            ensure_sqlite_schema(connection, self.config.table)
            rows = connection.execute(sql_select_users_query(self.config.table)).fetchall()
            return users_from_sql_rows([dict(row) for row in rows])

    def write(self, users: ProviderUsers) -> None:
        """Replace SQLite users with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        path = self.ensure_parent_dir()
        validate_sql_table(self.config.table)
        with closing(self._connect(path, write=True)) as connection:
            ensure_sqlite_schema(connection, self.config.table)
            upsert_sqlite_users(connection, self.config.table, users)
            delete_missing_sqlite_users(connection, self.config.table, users)
            connection.commit()
        chmod_private(path)

    def upsert_user(self, user: SFTPUser) -> None:
        """Create or update one SQLite user row.

        Parameters
        ----------
        user
            User to persist.
        """
        path = self.ensure_parent_dir()
        validate_sql_table(self.config.table)
        with closing(self._connect(path, write=True)) as connection:
            ensure_sqlite_schema(connection, self.config.table)
            upsert_sqlite_users(connection, self.config.table, ProviderUsers(users=[user]))
            connection.commit()
        chmod_private(path)

    def remove_user(self, username: str) -> None:
        """Remove one SQLite user row.

        Parameters
        ----------
        username
            Username to remove.
        """
        path = self.ensure_exists()
        validate_sql_table(self.config.table)
        with closing(self._connect(path, write=True)) as connection:
            ensure_sqlite_schema(connection, self.config.table)
            cursor = connection.execute(
                f"delete from {self.config.table} where username = ?",  # noqa: S608
                (username,),
            )
            if cursor.rowcount == 0:
                raise ProviderError(
                    f"Unknown user: {username}", suggestion="Run `sftpwarden users`."
                )
            connection.commit()

    def table_exists(self) -> bool:
        """Return whether the configured SQLite users table exists.

        Returns
        -------
        bool
            ``True`` when the table exists.
        """
        path = self.require_path()
        if not path.exists():
            return False
        validate_sql_table(self.config.table)
        with closing(self._connect(path)) as connection:
            row = connection.execute(
                "select 1 from sqlite_master where type = 'table' and name = ?",
                (self.config.table,),
            ).fetchone()
            return row is not None

    def create_table(self) -> None:
        """Create the configured SQLite users table."""
        path = self.ensure_parent_dir()
        validate_sql_table(self.config.table)
        with closing(self._connect(path, write=True)) as connection:
            ensure_sqlite_schema(connection, self.config.table)
            connection.commit()
        chmod_private(path)

    def _connect(self, path: Path, *, write: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma busy_timeout = 5000")
        if write:
            connection.execute("pragma journal_mode = delete")
        return connection


def ensure_sqlite_schema(
    connection: sqlite3.Connection,
    table: str = DEFAULT_SQL_USERS_TABLE,
) -> None:
    """Ensure the SQLite users table exists.

    Parameters
    ----------
    connection
        SQLite connection.
    table
        Users table name.
    """
    validate_sql_table(table)
    connection.execute(
        f"""
        create table if not exists {table} (
          username text primary key,
          public_keys text,
          password_hash text,
          uid integer,
          gid integer,
          upload_dir text not null default 'upload',
          comment text,
          disabled integer not null default 0
        )
        """  # noqa: S608
    )


def upsert_sqlite_users(connection: sqlite3.Connection, table: str, users: ProviderUsers) -> None:
    """Upsert users into SQLite.

    Parameters
    ----------
    connection
        SQLite connection.
    table
        Users table name.
    users
        Users to persist.
    """
    validate_sql_table(table)
    if not users.users:
        return
    columns = ", ".join(SQL_USER_COLUMNS)
    placeholders = ", ".join(["?"] * len(SQL_USER_COLUMNS))
    updates = ", ".join(
        f"{column}=excluded.{column}" for column in SQL_USER_COLUMNS if column != "username"
    )
    connection.executemany(
        f"insert into {table} ({columns}) values ({placeholders}) "  # noqa: S608
        f"on conflict(username) do update set {updates}",
        [sqlite_user_row(user) for user in users.users],
    )


def delete_missing_sqlite_users(
    connection: sqlite3.Connection, table: str, users: ProviderUsers
) -> None:
    """Delete SQLite users that are missing from a desired provider set.

    Parameters
    ----------
    connection
        SQLite connection.
    table
        Users table name.
    users
        Desired provider users.
    """
    validate_sql_table(table)
    usernames = [user.username for user in users.users]
    if not usernames:
        connection.execute(f"delete from {table}")  # noqa: S608
        return
    placeholders = ", ".join(["?"] * len(usernames))
    connection.execute(
        f"delete from {table} where username not in ({placeholders})",  # noqa: S608
        usernames,
    )


def sqlite_user_row(user: SFTPUser) -> tuple[Any, ...]:
    """Convert a user to SQLite row values.

    Parameters
    ----------
    user
        User to persist.

    Returns
    -------
    tuple[Any, ...]
        SQLite-compatible row values.
    """
    row = list(sql_user_row(user))
    row[-1] = int(user.disabled)
    return tuple(row)
