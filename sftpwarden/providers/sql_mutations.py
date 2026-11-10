"""Checked mutations executed inside the owning SQL provider transaction."""

from __future__ import annotations

from typing import Any

from sftpwarden.providers.mutations import prepare_user_mutation, require_unchanged_snapshot
from sftpwarden.providers.sql import (
    SQL_USER_COLUMNS,
    SQL_USER_KEY_COLUMNS,
    delete_missing_sql_users,
    delete_sql_user,
    replace_sql_user_keys_for_user,
    schema_uses_key_table,
    sql_user_keys_table,
    sql_user_row,
    upsert_sql_user,
    upsert_sql_users,
    users_from_sql_rows,
    validate_sql_table,
)
from sftpwarden.users.models import ProviderUsers, SFTPUser


def read_locked_users(
    cursor: Any, table: str, schema_version: int, *, username: str | None = None
) -> ProviderUsers:
    """Lock selected user/key rows and return a fresh comparison snapshot."""
    validate_sql_table(table)
    where = " where username = %s" if username is not None else ""
    params = [username] if username is not None else None
    cursor.execute(
        f"select {', '.join(SQL_USER_COLUMNS)} from {table}{where} "  # noqa: S608
        "order by username for update",
        params,
    )
    rows = list(cursor.fetchall())
    keys = None
    if schema_uses_key_table(schema_version):
        key_table = sql_user_keys_table(table)
        cursor.execute(
            f"select {', '.join(SQL_USER_KEY_COLUMNS)} from {key_table}{where} "  # noqa: S608
            "order by username, name for update",
            params,
        )
        keys = list(cursor.fetchall())
    return users_from_sql_rows(rows, key_rows=keys, schema_version=schema_version)


def mutate_sql_user(
    cursor: Any,
    table: str,
    expected: ProviderUsers,
    username: str,
    replacement: SFTPUser | None,
    *,
    dialect: str,
    source_schema: int,
    migrate_schema: bool,
) -> None:
    """Compare only the selected user; normal mutations never replace other users."""
    current = read_locked_users(
        cursor, table, source_schema, username=None if migrate_schema else username
    )
    merged, migrated = prepare_user_mutation(
        current, expected, username, replacement, migrate_schema=migrate_schema
    )
    if migrated:
        upsert_sql_users(
            cursor, table, merged, dialect=dialect, schema_version=merged.schema_version
        )
        delete_missing_sql_users(cursor, table, merged)
    elif replacement is None:
        delete_sql_user(cursor, table, username, schema_version=current.schema_version)
    elif not any(user.username == username for user in current.users):
        # A concurrent insert must conflict, never become an unconditional UPSERT.
        cursor.execute(
            f"insert into {table} ({', '.join(SQL_USER_COLUMNS)}) "  # noqa: S608
            f"values ({', '.join(['%s'] * len(SQL_USER_COLUMNS))})",
            sql_user_row(replacement),
        )
        if schema_uses_key_table(current.schema_version):
            replace_sql_user_keys_for_user(cursor, table, replacement, dialect=dialect)
    else:
        upsert_sql_user(
            cursor, table, replacement, dialect=dialect, schema_version=current.schema_version
        )


def replace_sql_snapshot(
    cursor: Any, table: str, expected: ProviderUsers, replacement: ProviderUsers, *, dialect: str
) -> None:
    """Reject a stale bulk input before changing any user/key rows."""
    require_unchanged_snapshot(read_locked_users(cursor, table, expected.schema_version), expected)
    upsert_sql_users(
        cursor, table, replacement, dialect=dialect, schema_version=replacement.schema_version
    )
    delete_missing_sql_users(cursor, table, replacement)
