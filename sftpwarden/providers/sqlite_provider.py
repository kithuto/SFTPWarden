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
    SQL_USER_KEY_COLUMNS,
    create_sql_user_keys_table_if_missing_statement,
    schema_uses_key_table,
    sql_select_user_keys_query,
    sql_select_users_query,
    sql_user_key_row,
    sql_user_keys_table,
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
            ensure_sqlite_schema(
                connection,
                self.config.table,
                schema_version=self.config.user_schema,
            )
            rows = connection.execute(sql_select_users_query(self.config.table)).fetchall()
            key_rows = None
            if schema_uses_key_table(self.config.user_schema):
                key_rows = connection.execute(
                    sql_select_user_keys_query(self.config.table)
                ).fetchall()
            return users_from_sql_rows(
                [dict(row) for row in rows],
                key_rows=[dict(row) for row in key_rows] if key_rows is not None else None,
                schema_version=self.config.user_schema,
            )

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
            ensure_sqlite_schema(connection, self.config.table, schema_version=users.schema_version)
            upsert_sqlite_users(
                connection,
                self.config.table,
                users,
                schema_version=users.schema_version,
            )
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
            ensure_sqlite_schema(
                connection,
                self.config.table,
                schema_version=self.config.user_schema,
            )
            upsert_sqlite_users(
                connection,
                self.config.table,
                ProviderUsers(schema_version=self.config.user_schema, users=[user]),
                schema_version=self.config.user_schema,
            )
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
            ensure_sqlite_schema(
                connection,
                self.config.table,
                schema_version=self.config.user_schema,
            )
            cursor = connection.execute(
                f"delete from {self.config.table} where username = ?",  # noqa: S608
                (username,),
            )
            if cursor.rowcount == 0:
                raise ProviderError(
                    f"Unknown user: {username}", suggestion="Run `sftpwarden users`."
                )
            if schema_uses_key_table(self.config.user_schema):
                key_table = sql_user_keys_table(self.config.table)
                connection.execute(
                    f"delete from {key_table} where username = ?",  # noqa: S608
                    (username,),
                )
            connection.commit()

    def table_exists(self) -> bool:
        """Return whether the configured SQLite provider storage exists.

        Returns
        -------
        bool
            ``True`` when all tables required by the active schema exist.
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
            if row is None:
                return False
            if not schema_uses_key_table(self.config.user_schema):
                return True
            key_table = sql_user_keys_table(self.config.table)
            key_row = connection.execute(
                "select 1 from sqlite_master where type = 'table' and name = ?",
                (key_table,),
            ).fetchone()
            return key_row is not None

    def create_table(self) -> None:
        """Create the configured SQLite users table."""
        path = self.ensure_parent_dir()
        validate_sql_table(self.config.table)
        with closing(self._connect(path, write=True)) as connection:
            ensure_sqlite_schema(
                connection,
                self.config.table,
                schema_version=self.config.user_schema,
            )
            connection.commit()
        chmod_private(path)

    def ensure_schema_storage(self, schema_version: int) -> None:
        """Ensure tables required by a user schema exist."""
        path = self.ensure_parent_dir()
        validate_sql_table(self.config.table)
        with closing(self._connect(path, write=True)) as connection:
            ensure_sqlite_schema(connection, self.config.table, schema_version=schema_version)
            connection.commit()
        chmod_private(path)

    def _connect(self, path: Path, *, write: bool = False) -> sqlite3.Connection:
        """Open a configured SQLite connection with safe runtime pragmas."""
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma busy_timeout = 5000")
        if write:
            connection.execute("pragma journal_mode = delete")
        return connection


def ensure_sqlite_schema(
    connection: sqlite3.Connection,
    table: str = DEFAULT_SQL_USERS_TABLE,
    *,
    schema_version: int = 1,
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
    if schema_uses_key_table(schema_version):
        key_statement = create_sql_user_keys_table_if_missing_statement(table)
        connection.execute(key_statement.replace(" boolean ", " integer "))


def upsert_sqlite_users(
    connection: sqlite3.Connection,
    table: str,
    users: ProviderUsers,
    *,
    schema_version: int = 1,
) -> None:
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
    uses_key_table = schema_uses_key_table(schema_version)
    if not users.users:
        if uses_key_table:
            replace_sqlite_user_keys(connection, table, users)
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
    if uses_key_table:
        replace_sqlite_user_keys(connection, table, users)


def replace_sqlite_user_keys(
    connection: sqlite3.Connection,
    table: str,
    users: ProviderUsers,
) -> None:
    """Replace schema v2 SQLite key rows for a desired user set."""
    key_table = sql_user_keys_table(table)
    connection.execute(f"delete from {key_table}")  # noqa: S608
    key_rows = [
        sqlite_user_key_row(user.username, key)
        for user in users.users
        for key in user.key_objects()
    ]
    if not key_rows:
        return
    columns = ", ".join(SQL_USER_KEY_COLUMNS)
    placeholders = ", ".join(["?"] * len(SQL_USER_KEY_COLUMNS))
    connection.executemany(
        f"insert into {key_table} ({columns}) values ({placeholders})",  # noqa: S608
        key_rows,
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


def sqlite_user_key_row(username: str, key: Any) -> tuple[Any, ...]:
    """Convert a key to SQLite-compatible row values."""
    row = list(sql_user_key_row(username, key))
    row[5] = int(key.disabled)
    return tuple(row)
