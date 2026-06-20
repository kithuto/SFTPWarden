from __future__ import annotations

import re
from typing import Any

from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError

DEFAULT_SQL_USERS_TABLE = "sftp_users"
SQL_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
SQL_MUTATION_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|replace)\b",
    re.IGNORECASE,
)
SQL_USER_COLUMNS = (
    "username",
    "public_keys",
    "password_hash",
    "uid",
    "gid",
    "upload_dir",
    "comment",
    "disabled",
)
SQL_CREATE_USER_COLUMNS = (
    "username varchar(32) primary key",
    "public_keys text",
    "password_hash text",
    "uid integer",
    "gid integer",
    "upload_dir varchar(255) not null default 'upload'",
    "comment text",
    "disabled boolean not null default false",
)


def validate_sql_table(table: str) -> None:
    """Validate a SQL table identifier.

    Parameters
    ----------
    table
        Table name or schema-qualified table name.

    Raises
    ------
    ProviderError
        Raised when the table name is unsafe.
    """
    if not SQL_TABLE_RE.fullmatch(table):
        raise ProviderError("SQL provider table name is invalid.")


def validate_sql_read_query(query: str) -> None:
    """Validate a custom SQL read query.

    Parameters
    ----------
    query
        SQL query supplied in provider config.

    Raises
    ------
    ProviderError
        Raised when the query is empty, multi-statement, or mutating.
    """
    normalized = query.strip()
    if not normalized:
        raise ProviderError("SQL provider query cannot be empty.")
    if ";" in normalized:
        raise ProviderError("SQL provider query must contain a single read-only statement.")
    first_word = normalized.split(None, 1)[0].lower()
    if first_word not in {"select", "with"}:
        raise ProviderError("SQL provider query must start with SELECT or WITH.")
    if SQL_MUTATION_RE.search(normalized):
        raise ProviderError("SQL provider query must be read-only.")


def sql_select_users_query(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    """Build the default SQL users query.

    Parameters
    ----------
    table
        Users table name.

    Returns
    -------
    str
        SQL select query.
    """
    validate_sql_table(table)
    return f"select {', '.join(SQL_USER_COLUMNS)} from {table} order by username"  # noqa: S608


def sql_check_table_query(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    """Build a lightweight SQL users table existence query.

    Parameters
    ----------
    table
        Users table name.

    Returns
    -------
    str
        Query that succeeds when the table exists.
    """
    validate_sql_table(table)
    return f"select 1 from {table} limit 1"  # noqa: S608


def create_sql_users_table_statement(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    """Build the SQL users table creation statement.

    Parameters
    ----------
    table
        Users table name.

    Returns
    -------
    str
        ``CREATE TABLE`` statement for the default provider schema.
    """
    validate_sql_table(table)
    columns = ", ".join(SQL_CREATE_USER_COLUMNS)
    return f"create table {table} ({columns})"  # noqa: S608


def users_from_sql_rows(rows: list[dict[str, Any]]) -> ProviderUsers:
    """Convert SQL result rows into provider users.

    Parameters
    ----------
    rows
        Mapping rows returned by a SQL driver.

    Returns
    -------
    ProviderUsers
        Validated provider users.
    """
    users: list[SFTPUser] = []
    for row in rows:
        public_keys_value = row.get("public_keys") or ""
        if isinstance(public_keys_value, str):
            public_keys = [key.strip() for key in public_keys_value.splitlines() if key.strip()]
        else:
            public_keys = [str(key).strip() for key in public_keys_value if str(key).strip()]
        users.append(
            SFTPUser(
                username=str(row["username"]),
                public_keys=public_keys,
                password_hash=row.get("password_hash") or None,
                uid=int(row["uid"]) if row.get("uid") is not None else None,
                gid=int(row["gid"]) if row.get("gid") is not None else None,
                upload_dir=str(row.get("upload_dir") or "upload"),
                comment=row.get("comment") or None,
                disabled=parse_sql_bool(row.get("disabled", False)),
            )
        )
    return ProviderUsers(users=users)


def parse_sql_bool(value: Any) -> bool:
    """Parse a database boolean value.

    Parameters
    ----------
    value
        Raw database value.

    Returns
    -------
    bool
        Parsed boolean value.
    """
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    return bool(value)


def sql_user_row(user: SFTPUser) -> tuple[Any, ...]:
    """Convert a user to a SQL parameter row.

    Parameters
    ----------
    user
        User to persist.

    Returns
    -------
    tuple[Any, ...]
        Row values matching ``SQL_USER_COLUMNS``.
    """
    return (
        user.username,
        "\n".join(user.public_keys),
        user.password_hash,
        user.uid,
        user.gid,
        user.upload_dir,
        user.comment,
        user.disabled,
    )


def upsert_sql_users(cursor: Any, table: str, users: ProviderUsers, *, dialect: str) -> None:
    """Upsert users with a SQL dialect-specific statement.

    Parameters
    ----------
    cursor
        Database cursor.
    table
        Users table name.
    users
        Users to persist.
    dialect
        SQL dialect, either ``mysql`` or ``postgres``.
    """
    validate_sql_table(table)
    if not users.users:
        return
    columns = ", ".join(SQL_USER_COLUMNS)
    placeholders = ", ".join(["%s"] * len(SQL_USER_COLUMNS))
    if dialect == "mysql":
        updates = ", ".join(
            f"{column}=values({column})" for column in SQL_USER_COLUMNS if column != "username"
        )
        statement = (
            f"insert into {table} ({columns}) values ({placeholders}) "  # noqa: S608
            f"on duplicate key update {updates}"
        )
    elif dialect == "postgres":
        updates = ", ".join(
            f"{column}=excluded.{column}" for column in SQL_USER_COLUMNS if column != "username"
        )
        statement = (
            f"insert into {table} ({columns}) values ({placeholders}) "  # noqa: S608
            f"on conflict (username) do update set {updates}"
        )
    else:
        raise ProviderError(f"Unsupported SQL dialect: {dialect}")
    cursor.executemany(statement, [sql_user_row(user) for user in users.users])


def upsert_sql_user(cursor: Any, table: str, user: SFTPUser, *, dialect: str) -> None:
    """Upsert a single user.

    Parameters
    ----------
    cursor
        Database cursor.
    table
        Users table name.
    user
        User to persist.
    dialect
        SQL dialect, either ``mysql`` or ``postgres``.
    """
    upsert_sql_users(cursor, table, ProviderUsers(users=[user]), dialect=dialect)


def delete_sql_user(cursor: Any, table: str, username: str) -> None:
    """Delete a single user from a SQL provider table.

    Parameters
    ----------
    cursor
        Database cursor.
    table
        Users table name.
    username
        Username to delete.
    """
    validate_sql_table(table)
    cursor.execute(f"delete from {table} where username = %s", [username])  # noqa: S608
    if getattr(cursor, "rowcount", 1) == 0:
        raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")


def delete_missing_sql_users(cursor: Any, table: str, users: ProviderUsers) -> None:
    """Delete SQL users that are missing from a desired provider set.

    Parameters
    ----------
    cursor
        Database cursor.
    table
        Users table name.
    users
        Desired provider users.
    """
    validate_sql_table(table)
    usernames = [user.username for user in users.users]
    if not usernames:
        cursor.execute(f"delete from {table}")  # noqa: S608
        return
    placeholders = ", ".join(["%s"] * len(usernames))
    cursor.execute(
        f"delete from {table} where username not in ({placeholders})",  # noqa: S608
        usernames,
    )
