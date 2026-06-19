from __future__ import annotations

import re
from typing import Any

from sftpwarden.utils.errors import ProviderError
from sftpwarden.users.models import ProviderUsers, SFTPUser

DEFAULT_SQL_USERS_TABLE = "sftp_users"
SQL_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
SQL_USER_COLUMNS = (
    "username",
    "public_keys",
    "password_hash",
    "uid",
    "gid",
    "upload_dir",
    "disabled",
)


def validate_sql_table(table: str) -> None:
    if not SQL_TABLE_RE.fullmatch(table):
        raise ProviderError("SQL provider table name is invalid.")


def sql_select_users_query(table: str = DEFAULT_SQL_USERS_TABLE) -> str:
    validate_sql_table(table)
    return f"select {', '.join(SQL_USER_COLUMNS)} from {table} order by username"  # noqa: S608


def users_from_sql_rows(rows: list[dict[str, Any]]) -> ProviderUsers:
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
                disabled=parse_sql_bool(row.get("disabled", False)),
            )
        )
    return ProviderUsers(users=users)


def parse_sql_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    return bool(value)


def sql_user_row(user: SFTPUser) -> tuple[Any, ...]:
    return (
        user.username,
        "\n".join(user.public_keys),
        user.password_hash,
        user.uid,
        user.gid,
        user.upload_dir,
        user.disabled,
    )


def upsert_sql_users(cursor: Any, table: str, users: ProviderUsers, *, dialect: str) -> None:
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
    upsert_sql_users(cursor, table, ProviderUsers(users=[user]), dialect=dialect)


def delete_sql_user(cursor: Any, table: str, username: str) -> None:
    validate_sql_table(table)
    cursor.execute(f"delete from {table} where username = %s", [username])  # noqa: S608
    if getattr(cursor, "rowcount", 1) == 0:
        raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")


def delete_missing_sql_users(cursor: Any, table: str, users: ProviderUsers) -> None:
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
