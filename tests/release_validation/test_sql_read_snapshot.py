from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from sftpwarden.config import load_config
from sftpwarden.providers import provider_for_config
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError

from .conftest import PUBLIC_KEY_1, PUBLIC_KEY_2, CleanupStack, ReleaseCli, assert_ok
from .test_database_providers import (
    DATABASE_CASES,
    DatabaseCase,
    init_when_database_is_ready,
    start_database_container,
)

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_databases,
]


@pytest.mark.parametrize(
    "case", DATABASE_CASES[:3], ids=[case.provider for case in DATABASE_CASES[:3]]
)
def test_t11_sql_users_and_keys_share_one_read_snapshot(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
    case: DatabaseCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = start_database_container(
        case, name=f"sftpwarden-snapshot-{unique_name}", host_port=0, cleanup_stack=cleanup_stack
    )
    root = tmp_path / "project"
    assert_ok(
        init_when_database_is_ready(
            cli, case=case, root=root, dsn=case.dsn_template.format(port=port)
        )
    )
    config = load_config(root / "sftpwarden.yaml")
    provider = provider_for_config(config.provider)
    original_user = SFTPUser(
        username="alice", keys=[SFTPUserKey(name="old", public_key=PUBLIC_KEY_1)]
    )
    replacement = SFTPUser(
        username="alice", disabled=True, keys=[SFTPUserKey(name="new", public_key=PUBLIC_KEY_2)]
    )
    provider.write(ProviderUsers(schema_version=2, users=[original_user]))
    if case.provider == "postgresql":
        import psycopg as driver
    else:
        import pymysql as driver
    connect = driver.connect
    connected = False
    injected = False

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
            self.statement = ""

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, statement, *args, **kwargs):
            self.statement = str(statement)
            return self.cursor.execute(statement, *args, **kwargs)

        def fetchall(self):
            nonlocal injected
            rows = self.cursor.fetchall()
            if not injected and "from sftp_users" in self.statement:
                injected = True
                # Commit a different connection after user rows, before key rows.
                provider.upsert_user(replacement)
            return rows

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def cursor(self, *args, **kwargs):
            return Cursor(self.connection.cursor(*args, **kwargs))

    def intercepted_connect(*args, **kwargs):
        nonlocal connected
        connection = connect(*args, **kwargs)
        if connected:
            return connection
        connected = True
        if case.provider != "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("set session transaction isolation level read committed")
        return Connection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(driver, "connect", intercepted_connect)
        observed = provider.read().users[0]
    assert injected
    assert observed == original_user, "Read mixed an old enabled user with newly committed keys"
    assert provider_for_config(config.provider).read().users[0] == replacement


@pytest.mark.parametrize(
    "case", DATABASE_CASES[:3], ids=[case.provider for case in DATABASE_CASES[:3]]
)
def test_invalid_sql_metadata_is_rejected_and_correction_is_visible(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
    case: DatabaseCase,
) -> None:
    port = start_database_container(
        case, name=f"sftpwarden-metadata-{unique_name}", host_port=0, cleanup_stack=cleanup_stack
    )
    root = tmp_path / "project"
    dsn = case.dsn_template.format(port=port)
    assert_ok(init_when_database_is_ready(cli, case=case, root=root, dsn=dsn))
    config = load_config(root / "sftpwarden.yaml")
    provider = provider_for_config(config.provider)
    original = ProviderUsers(
        schema_version=2,
        users=[
            SFTPUser(
                username="alice",
                keys=[
                    SFTPUserKey(
                        name="main",
                        public_key=PUBLIC_KEY_1,
                        disabled=True,
                        metadata={"owner": "operator"},
                    )
                ],
            )
        ],
    )
    provider.write(original)
    assert provider.read() == original

    def set_metadata(value: str) -> None:
        if case.provider == "postgresql":
            import psycopg

            connection = psycopg.connect(dsn)
        else:
            connection = provider._connect()
        with closing(connection), connection.cursor() as cursor:
            cursor.execute(
                "update sftp_user_keys set metadata = %s where username = %s", (value, "alice")
            )
            connection.commit()

    set_metadata("private-invalid-value")
    with pytest.raises(ProviderError, match="Invalid SQL provider") as error:
        provider.read()
    assert "private-invalid-value" not in str(error.value)
    set_metadata('{"owner": "operator"}')
    assert provider_for_config(config.provider).read() == original
