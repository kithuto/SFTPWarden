from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers.mysql_provider import (
    MariaDBProvider,
    MySQLProvider,
    mariadb_connect_kwargs,
    mysql_connect_kwargs,
)
from sftpwarden.providers.postgres_provider import PostgreSQLProvider
from sftpwarden.providers.sql import (
    delete_missing_sql_users,
    delete_sql_user,
    parse_sql_bool,
    replace_sql_user_keys_for_user,
    sql_user_keys_table,
    upsert_sql_user,
    upsert_sql_users,
    users_from_sql_rows,
)
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError

TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"
SECOND_TEST_KEY = "ssh-ed25519 ZmFrZS1zcWwtMg=="
TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


class FakeCursor:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        execute_error: Exception | None = None,
        executemany_error: Exception | None = None,
    ) -> None:
        self.rows = rows or []
        self.execute_error = execute_error
        self.executemany_error = executemany_error
        self.executed: list[tuple[str, Any]] = []
        self.executed_many: list[tuple[str, list[tuple[Any, ...]]]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, statement: str, params: Any = None) -> None:
        self.executed.append((statement, params))
        if self.execute_error is not None:
            raise self.execute_error

    def executemany(self, statement: str, rows: list[tuple[Any, ...]]) -> None:
        self.executed_many.append((statement, rows))
        if self.executemany_error is not None:
            raise self.executemany_error

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class SequentialFetchCursor(FakeCursor):
    def __init__(self, fetches: list[list[dict[str, Any]]]) -> None:
        super().__init__()
        self._fetches = list(fetches)

    def fetchall(self) -> list[dict[str, Any]]:
        return self._fetches.pop(0)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def sample_user(username: str = "alice") -> SFTPUser:
    return SFTPUser(
        username=username,
        password_hash="$6$rounds=500000$saltstring$hashvalue",  # noqa: S106
        comment="Finance dropbox",
    )


def sample_row(username: str = "alice") -> dict[str, Any]:
    return {
        "username": username,
        "public_keys": "",
        "password_hash": "$6$rounds=500000$saltstring$hashvalue",
        "uid": None,
        "gid": None,
        "upload_dir": "upload",
        "comment": "Finance dropbox",
        "disabled": False,
    }


def install_fake_pymysql(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    pymysql = types.ModuleType("pymysql")
    pymysql.cursors = types.SimpleNamespace(DictCursor=object)  # type: ignore[attr-defined]

    def connect(**kwargs: Any) -> FakeConnection:
        connection.kwargs = kwargs  # type: ignore[attr-defined]
        return connection

    pymysql.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymysql", pymysql)


def install_fake_psycopg(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    psycopg = types.ModuleType("psycopg")
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()  # type: ignore[attr-defined]

    def connect(dsn: str, **kwargs: Any) -> FakeConnection:
        connection.dsn = dsn  # type: ignore[attr-defined]
        connection.kwargs = kwargs  # type: ignore[attr-defined]
        return connection

    psycopg.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)


def mysql_provider(*, user_schema: int = 1) -> MySQLProvider:
    return MySQLProvider(
        config=ProviderConfig(
            type=ProviderType.MYSQL,
            dsn="mysql://user:pass@db.example.com:3307/sftp",
            table="sftp_users",
            user_schema=user_schema,
        )
    )


def mysql_provider_with_query(query: str) -> MySQLProvider:
    return MySQLProvider(
        config=ProviderConfig(
            type=ProviderType.MYSQL,
            dsn="mysql://user:pass@db.example.com:3307/sftp",
            query=query,
            table="sftp_users",
        )
    )


def mariadb_provider() -> MariaDBProvider:
    return MariaDBProvider(
        config=ProviderConfig(
            type=ProviderType.MARIADB,
            dsn="mariadb+pymysql://user:pass@db.example.com:3307/sftp",
            table="sftp_users",
        )
    )


def postgres_provider(*, user_schema: int = 1) -> PostgreSQLProvider:
    return PostgreSQLProvider(
        config=ProviderConfig(
            type=ProviderType.POSTGRESQL,
            dsn="postgresql://user:pass@db.example.com:5432/sftp",
            table="sftp_users",
            user_schema=user_schema,
        )
    )


def postgres_provider_with_query(query: str) -> PostgreSQLProvider:
    return PostgreSQLProvider(
        config=ProviderConfig(
            type=ProviderType.POSTGRESQL,
            dsn="postgresql://user:pass@db.example.com:5432/sftp",
            query=query,
            table="sftp_users",
        )
    )


def test_mysql_provider_reads_users_with_default_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rows=[sample_row()])
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    users = mysql_provider().read()

    assert users.users == [sample_user()]
    assert cursor.executed == [
        (
            "select username, public_keys, password_hash, uid, gid, upload_dir, "
            "comment, disabled from sftp_users order by username",
            None,
        )
    ]
    assert connection.closed
    assert connection.kwargs["cursorclass"] is object  # type: ignore[attr-defined]


def test_mysql_provider_reads_schema_v2_key_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = SequentialFetchCursor(
        [
            [sample_row()],
            [{"username": "alice", "name": "prod", "public_key": TEST_KEY}],
        ]
    )
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    users = mysql_provider(user_schema=2).read()

    assert users.schema_version == 2
    assert users.users[0].keys[0].name == "prod"
    assert cursor.executed[1][0].startswith("select username, name, public_key")


def test_sql_provider_empty_text_is_empty() -> None:
    assert MySQLProvider.empty_text() == ""
    assert PostgreSQLProvider.empty_text() == ""


def test_sql_user_keys_table_names_follow_users_table() -> None:
    assert sql_user_keys_table("sftp_users") == "sftp_user_keys"
    assert sql_user_keys_table("partners") == "partners_keys"
    assert sql_user_keys_table("tenant.sftp_users") == "tenant.sftp_user_keys"
    assert sql_user_keys_table("tenant.partners") == "tenant.partners_keys"


def test_sql_helpers_cover_list_keys_empty_upsert_and_delete_edges() -> None:
    users = users_from_sql_rows(
        [
            {
                **sample_row(),
                "public_keys": [" ssh-ed25519 AAAA alice@example.com ", ""],
                "uid": "12000",
                "gid": "12000",
                "disabled": "off",
            }
        ]
    )
    cursor = FakeCursor()

    upsert_sql_users(cursor, "sftp_users", ProviderUsers(users=[]), dialect="mysql")
    delete_missing_sql_users(cursor, "sftp_users", ProviderUsers(users=[]))

    cursor.rowcount = 0  # type: ignore[attr-defined]
    with pytest.raises(ProviderError, match="Unknown user"):
        delete_sql_user(cursor, "sftp_users", "missing")
    with pytest.raises(ProviderError, match="Unsupported SQL dialect"):
        upsert_sql_users(
            cursor, "sftp_users", ProviderUsers(users=[sample_user()]), dialect="sqlite"
        )

    assert users.users[0].public_keys == ["ssh-ed25519 AAAA alice@example.com"]
    assert users.users[0].uid == 12000
    assert not users.users[0].disabled
    assert parse_sql_bool("yes")
    assert not parse_sql_bool(0)
    assert cursor.executed[0] == ("delete from sftp_users", None)


def test_sql_helpers_handle_schema_v2_key_rows_and_replacements() -> None:
    users = users_from_sql_rows(
        [sample_row(), sample_row("bob")],
        schema_version=2,
        key_rows=[
            {"username": "ignored", "public_key": TEST_KEY},
            {
                "username": "alice",
                "name": "prod",
                "public_key": TEST_KEY,
                "disabled": "yes",
                "metadata": '{"env": "prod"}',
            },
            {
                "username": "bob",
                "name": "laptop",
                "public_key": SECOND_TEST_KEY,
                "metadata": "not-json",
            },
        ],
    )
    empty_cursor = FakeCursor()
    one_user_cursor = FakeCursor()
    delete_cursor = FakeCursor()

    upsert_sql_users(
        empty_cursor,
        "sftp_users",
        ProviderUsers(schema_version=2, users=[]),
        dialect="mysql",
        schema_version=2,
    )
    replace_sql_user_keys_for_user(one_user_cursor, "sftp_users", users.users[0], dialect="mysql")
    replace_sql_user_keys_for_user(
        one_user_cursor,
        "sftp_users",
        SFTPUser(username="empty", password_hash=TEST_HASH),
        dialect="mysql",
    )
    upsert_sql_user(
        one_user_cursor,
        "sftp_users",
        users.users[0],
        dialect="postgres",
        schema_version=2,
    )
    delete_sql_user(delete_cursor, "sftp_users", "alice", schema_version=2)

    assert users.schema_version == 2
    assert users.users[0].keys[0].metadata == {"env": "prod"}
    assert users.users[0].keys[0].disabled
    assert users.users[1].keys[0].metadata == {}
    assert empty_cursor.executed == [("delete from sftp_user_keys", None)]
    assert any(
        statement.startswith("insert into sftp_user_keys")
        for statement, _rows in one_user_cursor.executed_many
    )
    assert delete_cursor.executed[0] == (
        "delete from sftp_user_keys where username = %s",
        ["alice"],
    )


def test_mysql_provider_validates_and_executes_custom_read_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=[sample_row()])
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    users = mysql_provider_with_query("select * from sftp_users").read()

    assert users.users[0].username == "alice"
    assert cursor.executed == [("select * from sftp_users", None)]


def test_mysql_provider_mutates_users_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)
    provider = mysql_provider()

    provider.write(ProviderUsers(users=[sample_user()]))
    provider.upsert_user(sample_user("bob"))
    provider.remove_user("bob")
    provider.create_table()

    assert connection.committed
    assert connection.closed
    assert any("on duplicate key update" in statement for statement, _ in cursor.executed_many)
    assert ("delete from sftp_users where username = %s", ["bob"]) in cursor.executed
    assert any(statement.startswith("create table sftp_users") for statement, _ in cursor.executed)


def test_mysql_provider_create_table_creates_key_table_for_schema_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    mysql_provider(user_schema=2).create_table()

    assert any(statement.startswith("create table sftp_users") for statement, _ in cursor.executed)
    assert any(
        statement.startswith("create table sftp_user_keys") for statement, _ in cursor.executed
    )
    assert connection.committed


def test_mysql_provider_write_schema_v2_creates_missing_key_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)
    users = ProviderUsers(
        schema_version=2,
        users=[SFTPUser(username="alice", keys=[SFTPUserKey(name="prod", public_key=TEST_KEY)])],
    )

    mysql_provider(user_schema=1).write(users)

    assert cursor.executed[0][0].startswith("create table if not exists sftp_user_keys")
    assert any(statement == "delete from sftp_user_keys" for statement, _ in cursor.executed)
    assert any(
        statement.startswith("insert into sftp_user_keys") for statement, _ in cursor.executed_many
    )


def test_mysql_provider_rolls_back_failed_schema_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(execute_error=RuntimeError("create keys failed"))
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="create keys failed"):
        mysql_provider().ensure_schema_storage(2)

    assert connection.rolled_back
    assert connection.closed


def test_mysql_provider_rolls_back_failed_write(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(execute_error=RuntimeError("delete failed"))
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="delete failed"):
        mysql_provider().write(ProviderUsers(schema_version=1, users=[sample_user()]))

    assert connection.rolled_back
    assert connection.closed


def test_mysql_provider_rolls_back_failed_single_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(executemany_error=RuntimeError("upsert failed"))
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)
    with pytest.raises(RuntimeError, match="upsert failed"):
        mysql_provider().upsert_user(sample_user())
    assert connection.rolled_back

    cursor = FakeCursor(execute_error=RuntimeError("remove failed"))
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)
    with pytest.raises(RuntimeError, match="remove failed"):
        mysql_provider().remove_user("alice")
    assert connection.rolled_back

    cursor = FakeCursor(execute_error=RuntimeError("create failed"))
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)
    with pytest.raises(RuntimeError, match="create failed"):
        mysql_provider().create_table()
    assert connection.rolled_back


def test_mysql_provider_table_exists_handles_missing_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_table_error = Exception(1146, "table does not exist")
    cursor = FakeCursor(execute_error=missing_table_error)
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    assert not mysql_provider().table_exists()
    assert connection.closed


def test_mysql_provider_table_exists_reraises_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(execute_error=RuntimeError("network down"))
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="network down"):
        mysql_provider().table_exists()


def test_mysql_provider_table_exists_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    assert mysql_provider().table_exists()


def test_mysql_provider_table_exists_requires_key_table_for_schema_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingKeyTableCursor(FakeCursor):
        def execute(self, statement: str, params: Any = None) -> None:
            self.executed.append((statement, params))
            if statement.startswith("select 1 from sftp_user_keys"):
                raise Exception(1146, "table does not exist")

    cursor = MissingKeyTableCursor()
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)

    assert not mysql_provider(user_schema=2).table_exists()
    assert cursor.executed == [
        ("select 1 from sftp_users limit 1", None),
        ("select 1 from sftp_user_keys limit 1", None),
    ]


def test_mysql_connect_kwargs_rejects_non_mysql_scheme() -> None:
    with pytest.raises(ProviderError, match="mysql://"):
        mysql_connect_kwargs("postgresql://user:pass@example.com/sftp")


def test_mariadb_provider_reuses_pymysql_database_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=[sample_row()])
    connection = FakeConnection(cursor)
    install_fake_pymysql(monkeypatch, connection)
    provider = mariadb_provider()

    assert mariadb_connect_kwargs("mariadb://user:pass@example.com/sftp")["port"] == 3306
    assert provider.read().users == [sample_user()]
    provider.write(ProviderUsers(users=[sample_user("bob")]))

    assert connection.kwargs["database"] == "sftp"  # type: ignore[attr-defined]
    assert cursor.executed[0][0].startswith("select username")
    assert any("on duplicate key update" in statement for statement, _ in cursor.executed_many)


def test_mariadb_connect_kwargs_rejects_non_mariadb_scheme() -> None:
    with pytest.raises(ProviderError, match="mariadb://"):
        mariadb_connect_kwargs("mysql://user:pass@example.com/sftp")


@pytest.mark.parametrize(
    "method_name", ["read", "write", "upsert_user", "remove_user", "table_exists", "create_table"]
)
def test_mysql_provider_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    monkeypatch.setitem(sys.modules, "pymysql", None)
    provider = mysql_provider()
    args = {
        "read": (),
        "write": (ProviderUsers(users=[sample_user()]),),
        "upsert_user": (sample_user(),),
        "remove_user": ("alice",),
        "table_exists": (),
        "create_table": (),
    }[method_name]

    with pytest.raises(ProviderError, match="mysql optional dependency"):
        getattr(provider, method_name)(*args)


@pytest.mark.parametrize(
    "method_name", ["write", "upsert_user", "remove_user", "table_exists", "create_table"]
)
def test_mysql_provider_mutations_require_dsn(method_name: str) -> None:
    provider = MySQLProvider(
        config=ProviderConfig.model_construct(type=ProviderType.MYSQL, dsn=None, table="sftp_users")
    )
    args = {
        "write": (ProviderUsers(users=[sample_user()]),),
        "upsert_user": (sample_user(),),
        "remove_user": ("alice",),
        "table_exists": (),
        "create_table": (),
    }[method_name]

    with pytest.raises(ProviderError, match="requires dsn|mutations require dsn"):
        getattr(provider, method_name)(*args)


def test_postgres_provider_reads_users_with_default_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rows=[sample_row()])
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    users = postgres_provider().read()

    assert users.users == [sample_user()]
    assert cursor.executed[0][0].startswith("select username, public_keys")
    assert connection.dsn == "postgresql://user:pass@db.example.com:5432/sftp"  # type: ignore[attr-defined]
    assert "row_factory" in connection.kwargs  # type: ignore[attr-defined]


def test_postgres_provider_reads_schema_v2_key_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = SequentialFetchCursor(
        [
            [sample_row()],
            [{"username": "alice", "name": "prod", "public_key": TEST_KEY}],
        ]
    )
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    users = postgres_provider(user_schema=2).read()

    assert users.schema_version == 2
    assert users.users[0].keys[0].name == "prod"
    assert cursor.executed[1][0].startswith("select username, name, public_key")


def test_postgres_provider_validates_and_executes_custom_read_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=[sample_row()])
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    users = postgres_provider_with_query("select * from sftp_users").read()

    assert users.users[0].username == "alice"
    assert cursor.executed == [("select * from sftp_users", None)]


def test_postgres_provider_mutates_users(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)
    provider = postgres_provider()

    provider.write(ProviderUsers(users=[sample_user()]))
    provider.upsert_user(sample_user("bob"))
    provider.remove_user("bob")
    provider.create_table()

    assert any("on conflict (username)" in statement for statement, _ in cursor.executed_many)
    assert ("delete from sftp_users where username = %s", ["bob"]) in cursor.executed
    assert any(statement.startswith("create table sftp_users") for statement, _ in cursor.executed)


def test_postgres_provider_create_table_creates_key_table_for_schema_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    postgres_provider(user_schema=2).create_table()

    assert any(statement.startswith("create table sftp_users") for statement, _ in cursor.executed)
    assert any(
        statement.startswith("create table sftp_user_keys") for statement, _ in cursor.executed
    )


def test_postgres_provider_write_schema_v2_creates_missing_key_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)
    users = ProviderUsers(
        schema_version=2,
        users=[SFTPUser(username="alice", keys=[SFTPUserKey(name="prod", public_key=TEST_KEY)])],
    )

    postgres_provider(user_schema=1).write(users)

    assert cursor.executed[0][0].startswith("create table if not exists sftp_user_keys")
    assert any(statement == "delete from sftp_user_keys" for statement, _ in cursor.executed)
    assert any(
        statement.startswith("insert into sftp_user_keys") for statement, _ in cursor.executed_many
    )


def test_postgres_provider_table_exists_handles_missing_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingTableError(Exception):
        sqlstate = "42P01"

    cursor = FakeCursor(execute_error=MissingTableError())
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    assert not postgres_provider().table_exists()


def test_postgres_provider_table_exists_reraises_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(execute_error=RuntimeError("network down"))
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="network down"):
        postgres_provider().table_exists()


def test_postgres_provider_table_exists_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    assert postgres_provider().table_exists()


def test_postgres_provider_table_exists_requires_key_table_for_schema_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingTableError(Exception):
        sqlstate = "42P01"

    class MissingKeyTableCursor(FakeCursor):
        def execute(self, statement: str, params: Any = None) -> None:
            self.executed.append((statement, params))
            if statement.startswith("select 1 from sftp_user_keys"):
                raise MissingTableError()

    cursor = MissingKeyTableCursor()
    connection = FakeConnection(cursor)
    install_fake_psycopg(monkeypatch, connection)

    assert not postgres_provider(user_schema=2).table_exists()
    assert cursor.executed == [
        ("select 1 from sftp_users limit 1", None),
        ("select 1 from sftp_user_keys limit 1", None),
    ]


@pytest.mark.parametrize(
    "method_name",
    [
        "read",
        "write",
        "upsert_user",
        "remove_user",
        "table_exists",
        "create_table",
        "ensure_schema_storage",
    ],
)
def test_postgres_provider_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg.rows", None)
    provider = postgres_provider()
    args = {
        "read": (),
        "write": (ProviderUsers(schema_version=1, users=[sample_user()]),),
        "upsert_user": (sample_user(),),
        "remove_user": ("alice",),
        "table_exists": (),
        "create_table": (),
        "ensure_schema_storage": (2,),
    }[method_name]

    with pytest.raises(ProviderError, match="postgres optional dependency"):
        getattr(provider, method_name)(*args)


@pytest.mark.parametrize(
    "method_name",
    [
        "write",
        "upsert_user",
        "remove_user",
        "table_exists",
        "create_table",
        "ensure_schema_storage",
    ],
)
def test_postgres_provider_mutations_require_dsn(method_name: str) -> None:
    provider = PostgreSQLProvider(
        config=ProviderConfig.model_construct(
            type=ProviderType.POSTGRESQL, dsn=None, table="sftp_users"
        )
    )
    args = {
        "write": (ProviderUsers(users=[sample_user()]),),
        "upsert_user": (sample_user(),),
        "remove_user": ("alice",),
        "table_exists": (),
        "create_table": (),
        "ensure_schema_storage": (2,),
    }[method_name]

    with pytest.raises(ProviderError, match="requires dsn|mutations require dsn"):
        getattr(provider, method_name)(*args)


@pytest.mark.parametrize(
    ("provider_cls", "provider_type", "message"),
    [
        (MySQLProvider, ProviderType.MYSQL, "MySQL provider requires dsn"),
        (PostgreSQLProvider, ProviderType.POSTGRESQL, "PostgreSQL provider requires dsn"),
    ],
)
def test_sql_providers_require_dsn(provider_cls, provider_type: ProviderType, message: str) -> None:
    provider = provider_cls(config=ProviderConfig.model_construct(type=provider_type, dsn=None))

    with pytest.raises(ProviderError, match=message):
        provider.read()
