from __future__ import annotations

import json
import re
from typing import Any

from sftpwarden.users.models import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.users.schemas import NAMED_KEYS, user_schema
from sftpwarden.utils.errors import ProviderError

DEFAULT_SQL_USERS_TABLE = "sftp_users"
SQL_TABLE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$", flags=re.ASCII)
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
SQL_USER_KEY_COLUMNS = (
    "username",
    "name",
    "public_key",
    "fingerprint",
    "comment",
    "disabled",
    "created_at",
    "updated_at",
    "expires_at",
    "source",
    "metadata",
)
SQL_CREATE_USER_KEY_COLUMNS = (
    "username varchar(32) not null",
    "name varchar(64) not null",
    "public_key text not null",
    "fingerprint varchar(128) not null",
    "comment text",
    "disabled boolean not null default false",
    "created_at text",
    "updated_at text",
    "expires_at text",
    "source text",
    "metadata text",
    "primary key (username, name)",
    "unique (fingerprint)",
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


def sql_user_keys_table(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    """Return the schema v2 key table name for a users table."""
    validate_sql_table(table)
    if "." in table:
        schema_name, table_name = table.rsplit(".", 1)
        keys_name = (
            "sftp_user_keys" if table_name == DEFAULT_SQL_USERS_TABLE else f"{table_name}_keys"
        )
        key_table = f"{schema_name}.{keys_name}"
    else:
        key_table = "sftp_user_keys" if table == DEFAULT_SQL_USERS_TABLE else f"{table}_keys"
    validate_sql_table(key_table)
    return key_table


def sql_select_user_keys_query(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    """Build the default schema v2 SQL user keys query."""
    key_table = sql_user_keys_table(table)
    return (
        f"select {', '.join(SQL_USER_KEY_COLUMNS)} from {key_table} "  # noqa: S608
        "order by username, name"
    )


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


def create_sql_user_keys_table_statement(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    """Build the SQL key table creation statement for schema v2."""
    key_table = sql_user_keys_table(table)
    columns = ", ".join(SQL_CREATE_USER_KEY_COLUMNS)
    return f"create table {key_table} ({columns})"  # noqa: S608


def create_sql_user_keys_table_if_missing_statement(
    table: str = DEFAULT_SQL_USERS_TABLE,
) -> str:
    """Build an idempotent SQL key table creation statement for schema migrations."""
    return create_sql_user_keys_table_statement(table).replace(
        "create table ",
        "create table if not exists ",
        1,
    )


def schema_uses_key_table(schema_version: int) -> bool:
    """Return whether a user schema stores named keys in a key table."""
    return user_schema(schema_version).supports(NAMED_KEYS)


def users_from_sql_rows(
    rows: list[dict[str, Any]],
    *,
    key_rows: list[dict[str, Any]] | None = None,
    schema_version: int = 1,
) -> ProviderUsers:
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
    keys_by_username: dict[str, list[SFTPUserKey]] = {}
    uses_key_table = schema_uses_key_table(schema_version)
    if uses_key_table:
        for row in key_rows or []:
            if "name" not in row or "public_key" not in row:
                continue
            metadata_value = row.get("metadata") or "{}"
            try:
                metadata = (
                    json.loads(metadata_value)
                    if isinstance(metadata_value, str)
                    else metadata_value
                )
            except json.JSONDecodeError:
                metadata = {}
            key = SFTPUserKey(
                name=str(row["name"]),
                public_key=str(row["public_key"]),
                fingerprint=row.get("fingerprint") or None,
                comment=row.get("comment") or None,
                disabled=parse_sql_bool(row.get("disabled", False)),
                created_at=row.get("created_at") or None,
                updated_at=row.get("updated_at") or None,
                expires_at=row.get("expires_at") or None,
                source=row.get("source") or None,
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            keys_by_username.setdefault(str(row["username"]), []).append(key)
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
                public_keys=[] if uses_key_table else public_keys,
                keys=keys_by_username.get(str(row["username"]), []),
                password_hash=row.get("password_hash") or None,
                uid=int(row["uid"]) if row.get("uid") is not None else None,
                gid=int(row["gid"]) if row.get("gid") is not None else None,
                upload_dir=str(row.get("upload_dir") or "upload"),
                comment=row.get("comment") or None,
                disabled=parse_sql_bool(row.get("disabled", False)),
            )
        )
    return ProviderUsers(schema_version=user_schema(schema_version).version, users=users)


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


def sql_user_key_row(username: str, key: SFTPUserKey) -> tuple[Any, ...]:
    """Convert a key to SQL parameter values."""
    return (
        username,
        key.name,
        key.public_key,
        key.fingerprint,
        key.comment,
        key.disabled,
        key.created_at.isoformat() if key.created_at else None,
        key.updated_at.isoformat() if key.updated_at else None,
        key.expires_at.isoformat() if key.expires_at else None,
        key.source,
        json.dumps(key.metadata, sort_keys=True) if key.metadata else None,
    )


def execute_validated_sql(cursor: Any, statement: str, params: Any | None = None) -> Any:
    """Execute a SQL statement that has already passed local validation.

    Parameters
    ----------
    cursor
        Database cursor.
    statement
        SQL statement generated by SFTPWarden or validated with
        ``validate_sql_read_query``.
    params
        Optional query parameters.

    Returns
    -------
    Any
        Driver-specific execute result.
    """
    if params is None:
        return cursor.execute(statement)
    return cursor.execute(statement, params)


def upsert_sql_users(
    cursor: Any,
    table: str,
    users: ProviderUsers,
    *,
    dialect: str,
    schema_version: int = 1,
) -> None:
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
    uses_key_table = schema_uses_key_table(schema_version)
    if not users.users:
        if uses_key_table:
            replace_sql_user_keys(cursor, table, users, dialect=dialect)
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
    if uses_key_table:
        replace_sql_user_keys(cursor, table, users, dialect=dialect)


def replace_sql_user_keys(cursor: Any, table: str, users: ProviderUsers, *, dialect: str) -> None:
    """Replace schema v2 key rows for a complete desired user set."""
    key_table = sql_user_keys_table(table)
    execute_validated_sql(cursor, f"delete from {key_table}")  # noqa: S608
    key_rows = [
        sql_user_key_row(user.username, key) for user in users.users for key in user.key_objects()
    ]
    if not key_rows:
        return
    columns = ", ".join(SQL_USER_KEY_COLUMNS)
    placeholders = ", ".join(["%s"] * len(SQL_USER_KEY_COLUMNS))
    statement = f"insert into {key_table} ({columns}) values ({placeholders})"  # noqa: S608
    cursor.executemany(statement, key_rows)


def replace_sql_user_keys_for_user(
    cursor: Any,
    table: str,
    user: SFTPUser,
    *,
    dialect: str,
) -> None:
    """Replace schema v2 key rows for one user."""
    key_table = sql_user_keys_table(table)
    execute_validated_sql(
        cursor,
        f"delete from {key_table} where username = %s",  # noqa: S608
        [user.username],
    )
    key_rows = [sql_user_key_row(user.username, key) for key in user.key_objects()]
    if not key_rows:
        return
    columns = ", ".join(SQL_USER_KEY_COLUMNS)
    placeholders = ", ".join(["%s"] * len(SQL_USER_KEY_COLUMNS))
    statement = f"insert into {key_table} ({columns}) values ({placeholders})"  # noqa: S608
    cursor.executemany(statement, key_rows)


def upsert_sql_user(
    cursor: Any,
    table: str,
    user: SFTPUser,
    *,
    dialect: str,
    schema_version: int = 1,
) -> None:
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
    upsert_sql_users(
        cursor,
        table,
        ProviderUsers(schema_version=schema_version, users=[user]),
        dialect=dialect,
        schema_version=schema_version,
    )


def delete_sql_user(
    cursor: Any,
    table: str,
    username: str,
    *,
    schema_version: int = 1,
) -> None:
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
    if schema_uses_key_table(schema_version):
        key_table = sql_user_keys_table(table)
        execute_validated_sql(
            cursor,
            f"delete from {key_table} where username = %s",  # noqa: S608
            [username],
        )
    execute_validated_sql(cursor, f"delete from {table} where username = %s", [username])  # noqa: S608
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
        execute_validated_sql(cursor, f"delete from {table}")  # noqa: S608
        return
    placeholders = ", ".join(["%s"] * len(usernames))
    execute_validated_sql(
        cursor,
        f"delete from {table} where username not in ({placeholders})",  # noqa: S608
        usernames,
    )
