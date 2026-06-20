from __future__ import annotations

import pytest

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import (
    CSVProvider,
    MySQLProvider,
    PostgreSQLProvider,
    ProviderUsers,
    YAMLProvider,
    mysql_connect_kwargs,
    provider_class,
    users_from_sql_rows,
)
from sftpwarden.providers.sql import (
    delete_missing_sql_users,
    sql_select_users_query,
    upsert_sql_users,
    validate_sql_read_query,
)
from sftpwarden.users import SFTPUser
from sftpwarden.utils.errors import ProviderError


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
                "comment": "Finance dropbox",
                "disabled": False,
            }
        ]
    )

    assert users.users[0].username == "alice"
    assert users.users[0].uid == 10000
    assert users.users[0].comment == "Finance dropbox"


def test_mysql_connect_kwargs_parses_url() -> None:
    kwargs = mysql_connect_kwargs("mysql+pymysql://user:pass@example.com:3307/sftp")

    assert kwargs["host"] == "example.com"
    assert kwargs["port"] == 3307
    assert kwargs["user"] == "user"
    assert kwargs["password"] == "pass"
    assert kwargs["database"] == "sftp"


def test_provider_registry_returns_registered_classes() -> None:
    assert provider_class("yaml") is YAMLProvider
    assert provider_class(ProviderType.CSV) is CSVProvider
    assert provider_class(ProviderType.MYSQL) is MySQLProvider
    assert provider_class(ProviderType.POSTGRESQL) is PostgreSQLProvider


def test_csv_provider_round_trip(tmp_path) -> None:
    path = tmp_path / "users.csv"
    provider = CSVProvider(config=ProviderConfig(type=ProviderType.CSV), path=path)
    users = ProviderUsers(
        users=[
            SFTPUser(
                username="alice",
                password_hash="$6$rounds=500000$saltstring$hashvalue",  # noqa: S106
                uid=10001,
                gid=10002,
                upload_dir="dropbox",
                comment="Finance dropbox",
            )
        ]
    )

    provider.write(users)
    loaded = provider.read()

    assert loaded.users[0].username == "alice"
    assert loaded.users[0].uid == 10001
    assert loaded.users[0].gid == 10002
    assert loaded.users[0].upload_dir == "dropbox"
    assert loaded.users[0].comment == "Finance dropbox"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_file_provider_reports_missing_file(tmp_path) -> None:
    provider = YAMLProvider(
        config=ProviderConfig(type=ProviderType.YAML),
        path=tmp_path / "missing.yaml",
    )

    with pytest.raises(ProviderError, match="Provider file not found"):
        provider.read()


def test_sql_default_read_query_uses_users_table() -> None:
    assert sql_select_users_query() == (
        "select username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled "
        "from sftp_users order by username"
    )


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


def test_sql_read_query_allows_select_and_with() -> None:
    validate_sql_read_query("select * from sftp_users")
    validate_sql_read_query("with active as (select * from sftp_users) select * from active")


@pytest.mark.parametrize(
    "query",
    [
        "",
        "select * from sftp_users; drop table sftp_users",
        "delete from sftp_users",
        "with removed as (delete from sftp_users returning *) select * from removed",
    ],
)
def test_sql_read_query_rejects_mutations(query: str) -> None:
    with pytest.raises(ProviderError):
        validate_sql_read_query(query)
