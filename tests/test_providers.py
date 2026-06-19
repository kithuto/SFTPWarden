from __future__ import annotations

import pytest

from sftpwarden.config import ProviderType
from sftpwarden.utils.errors import ProviderError
from sftpwarden.providers import (
    MySQLProvider,
    ProviderUsers,
    YAMLProvider,
    mysql_connect_kwargs,
    provider_class,
    users_from_sql_rows,
)
from sftpwarden.providers.sql import delete_missing_sql_users, upsert_sql_users
from sftpwarden.users import SFTPUser


def test_users_from_sql_rows() -> None:
    users = users_from_sql_rows(
        [
            {
                "username": "alice",
                "public_keys": "",
                "password_hash": "$6$rounds=500000$saltstring$hashvalue",
                "uid": 10000,
                "gid": 10000,
                "upload_dir": "upload",
                "disabled": False,
            }
        ]
    )

    assert users.users[0].username == "alice"
    assert users.users[0].uid == 10000


def test_mysql_connect_kwargs_parses_url() -> None:
    kwargs = mysql_connect_kwargs("mysql+pymysql://user:pass@example.com:3307/sftp")

    assert kwargs["host"] == "example.com"
    assert kwargs["port"] == 3307
    assert kwargs["user"] == "user"
    assert kwargs["password"] == "pass"
    assert kwargs["database"] == "sftp"


def test_provider_registry_returns_registered_classes() -> None:
    assert provider_class("yaml") is YAMLProvider
    assert provider_class(ProviderType.MYSQL) is MySQLProvider


class RecordingCursor:
    def __init__(self) -> None:
        self.executed_many = []
        self.executed = []

    def executemany(self, statement, rows) -> None:
        self.executed_many.append((statement, rows))

    def execute(self, statement, params=None) -> None:
        self.executed.append((statement, params))


def test_sql_mutation_upserts_and_deletes_missing_users() -> None:
    cursor = RecordingCursor()
    users = ProviderUsers(
        users=[
            SFTPUser(
                username="alice",
                password_hash="$6$rounds=500000$saltstring$hashvalue",  # noqa: S106
            )
        ]
    )

    upsert_sql_users(cursor, "sftp_users", users, dialect="mysql")
    delete_missing_sql_users(cursor, "sftp_users", users)

    assert "on duplicate key update" in cursor.executed_many[0][0]
    assert cursor.executed_many[0][1][0][0] == "alice"
    assert cursor.executed == [("delete from sftp_users where username not in (%s)", ["alice"])]


def test_sql_mutation_rejects_bad_table_name() -> None:
    cursor = RecordingCursor()
    users = ProviderUsers(users=[])
    with pytest.raises(ProviderError, match="table name is invalid"):
        delete_missing_sql_users(cursor, "sftp_users; drop table sftp_users", users)
