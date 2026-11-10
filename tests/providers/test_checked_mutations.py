from __future__ import annotations

import pytest

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import MongoDBProvider
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.users.schemas import migrate_provider_users
from sftpwarden.utils.errors import ProviderError

from .test_sql_providers import (
    FakeConnection,
    SequentialFetchCursor,
    install_fake_psycopg,
    install_fake_pymysql,
    mysql_provider,
    postgres_provider,
    sample_row,
    sample_user,
)


@pytest.mark.parametrize("postgres", [False, True])
@pytest.mark.parametrize("operation", ["create", "update", "delete", "bulk", "migrate"])
def test_sql_checked_operations_use_current_rows_and_transactions(
    monkeypatch: pytest.MonkeyPatch, postgres: bool, operation: str
) -> None:
    creating = operation == "create"
    current = ProviderUsers(schema_version=1, users=[] if creating else [sample_user()])
    cursor = SequentialFetchCursor([[] if creating else [sample_row()]])
    connection = FakeConnection(cursor)
    install = install_fake_psycopg if postgres else install_fake_pymysql
    install(monkeypatch, connection)
    provider = postgres_provider() if postgres else mysql_provider()
    replacement = sample_user().model_copy(update={"comment": "changed"})
    if operation == "bulk":
        provider.replace_if_unchanged(current, ProviderUsers(schema_version=1, users=[replacement]))
    elif operation == "migrate":
        migrated = migrate_provider_users(current, to_version=2)
        provider.mutate_user(migrated, "alice", replacement, migrate_schema=True)
    else:
        provider.mutate_user(current, "alice", None if operation == "delete" else replacement)
    statements = [statement for statement, _ in cursor.executed]
    assert "set transaction isolation level serializable" in statements
    locked_reads = [statement for statement in statements if statement.startswith("select")]
    assert locked_reads and all(statement.endswith("for update") for statement in locked_reads)
    if creating:
        inserts = [statement for statement in statements if statement.startswith("insert")]
        assert len(inserts) == 1 and "conflict" not in inserts[0] and "duplicate" not in inserts[0]
    if operation in {"create", "update", "delete"}:
        assert all("where username = %s" in statement for statement in locked_reads)
        assert not any(statement == "delete from sftp_user_keys" for statement in statements)
    if not postgres:
        assert connection.committed and connection.closed


@pytest.mark.parametrize("postgres", [False, True])
@pytest.mark.parametrize("bulk", [False, True])
def test_sql_stale_edit_aborts_before_writing(
    monkeypatch: pytest.MonkeyPatch, postgres: bool, bulk: bool
) -> None:
    cursor = SequentialFetchCursor([[{**sample_row(), "comment": "newer metadata"}]])
    connection = FakeConnection(cursor)
    (install_fake_psycopg if postgres else install_fake_pymysql)(monkeypatch, connection)
    provider = postgres_provider() if postgres else mysql_provider()
    stale = ProviderUsers(schema_version=1, users=[sample_user()])
    with pytest.raises(ProviderError, match="conflict"):
        if bulk:
            provider.replace_if_unchanged(stale, ProviderUsers(schema_version=1, users=[]))
        else:
            provider.mutate_user(stale, "alice", None)
    assert not cursor.executed_many
    assert not any(sql.startswith(("insert", "delete", "update")) for sql, _ in cursor.executed)
    if not postgres:
        assert connection.rolled_back and connection.closed and not connection.committed


@pytest.mark.parametrize("postgres", [False, True])
def test_sql_storage_failure_is_sanitized_and_not_committed(
    monkeypatch: pytest.MonkeyPatch, postgres: bool
) -> None:
    cursor = SequentialFetchCursor([[sample_row()]])
    cursor.executemany_error = RuntimeError("synthetic-secret-dsn")
    connection = FakeConnection(cursor)
    (install_fake_psycopg if postgres else install_fake_pymysql)(monkeypatch, connection)
    provider = postgres_provider() if postgres else mysql_provider()
    with pytest.raises(ProviderError, match="failed or conflicted") as failure:
        provider.mutate_user(
            ProviderUsers(schema_version=1, users=[sample_user()]), "alice", sample_user()
        )
    assert "synthetic-secret" not in str(failure.value)
    if not postgres:
        assert connection.rolled_back and connection.closed and not connection.committed


@pytest.mark.parametrize("remove", [False, True])
def test_mongodb_compare_and_swap_detects_write_between_read_and_update(
    install_fake_pymongo, monkeypatch: pytest.MonkeyPatch, remove: bool
) -> None:
    install_fake_pymongo()
    provider = MongoDBProvider(
        ProviderConfig(type=ProviderType.MONGODB, dsn="mongodb://localhost/test")
    )
    provider.upsert_user(SFTPUser(username="alice"))
    before = provider.read()
    database = provider._database()
    collection = database[provider.config.collection]
    database.client.close()
    original = collection.find_one

    def read_then_external_edit(query):
        document = original(query)
        collection.documents["alice"]["comment"] = "external edit"
        return document

    monkeypatch.setattr(collection, "find_one", read_then_external_edit)
    with pytest.raises(ProviderError, match="conflict"):
        provider.mutate_user(before, "alice", None if remove else before.users[0])
    assert provider.read().users[0].comment == "external edit"


def test_mongodb_checked_replacement_and_delete(install_fake_pymongo) -> None:
    install_fake_pymongo()
    provider = MongoDBProvider(
        ProviderConfig(type=ProviderType.MONGODB, dsn="mongodb://localhost/test")
    )
    provider.upsert_user(SFTPUser(username="alice"))
    before = provider.read()
    replacement = before.users[0].model_copy(update={"comment": "new metadata"})
    provider.mutate_user(before, "alice", replacement)
    with pytest.raises(ProviderError, match="conflict"):
        provider.replace_if_unchanged(before, ProviderUsers(users=[]))
    current = provider.read()
    provider.replace_if_unchanged(current, current)
    provider.mutate_user(current, "alice", None)
    assert provider.read().users == []
    with pytest.raises(ProviderError, match="Unknown user"):
        provider.mutate_user(provider.read(), "alice", None)


@pytest.mark.parametrize("postgres", [False, True])
@pytest.mark.parametrize("always_fail", [False, True])
def test_sql_retries_only_aborted_transactions_with_a_finite_budget(
    monkeypatch: pytest.MonkeyPatch, postgres: bool, always_fail: bool
) -> None:
    provider = postgres_provider() if postgres else mysql_provider()
    calls = []

    class SerializationFailure(Exception):
        sqlstate = "40001"

    def transaction(operation):
        calls.append(operation)
        if always_fail or len(calls) < 2:
            raise SerializationFailure(1213, "synthetic driver detail")

    monkeypatch.setattr(provider, "_mutation_transaction", transaction)
    if always_fail:
        with pytest.raises(ProviderError, match="failed or conflicted"):
            provider.mutate_user(ProviderUsers(schema_version=1, users=[]), "alice", sample_user())
        assert len(calls) == 3
    else:
        provider.mutate_user(ProviderUsers(schema_version=1, users=[]), "alice", sample_user())
        assert len(calls) == 2
    assert all(operation is calls[0] for operation in calls)
