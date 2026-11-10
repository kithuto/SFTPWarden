import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import SQLiteProvider
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea"
SECOND_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGv5cJFgm0ffn6oHXkIseBv94l3DX7v+2vbF0+qWC+M6"


def provider(path: Path) -> SQLiteProvider:
    return SQLiteProvider(config=ProviderConfig(type=ProviderType.SQLITE, user_schema=2), path=path)


def named_user(name: str, key_name: str = "original") -> SFTPUser:
    public_key = SECOND_KEY if name == "bob" else KEY
    return SFTPUser(username=name, keys=[SFTPUserKey(name=key_name, public_key=public_key)])


def test_t10_single_user_update_preserves_other_users_keys(tmp_path: Path) -> None:
    path = tmp_path / "users.sqlite"
    db = provider(path)
    db.write(ProviderUsers(users=[named_user("alice"), named_user("bob")]))
    db.upsert_user(named_user("alice", "replacement"))
    users = {u.username: u for u in provider(path).read().users}
    assert [key.name for key in users["bob"].keys] == ["original"]
    assert [key.name for key in users["alice"].keys] == ["replacement"]


def test_t10_failed_key_insert_rolls_back_user_and_keys(tmp_path: Path) -> None:
    path = tmp_path / "users.sqlite"
    db = provider(path)
    db.write(ProviderUsers(users=[named_user("alice"), named_user("bob")]))
    before = db.read()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER reject_key BEFORE INSERT ON sftp_user_keys "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        db.upsert_user(named_user("alice", "replacement"))
    assert db.read() == before


def test_t10_full_replacement_removes_absent_users_and_all_their_keys(tmp_path: Path) -> None:
    path = tmp_path / "users.sqlite"
    db = provider(path)
    db.write(ProviderUsers(users=[named_user("alice"), named_user("bob")]))
    db.write(ProviderUsers(users=[named_user("alice")]))
    assert [user.username for user in db.read().users] == ["alice"]
    db.write(ProviderUsers(users=[]))
    assert db.read().users == []
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("select count(*) from sftp_user_keys").fetchone()[0] == 0


def test_t11_read_never_initializes_missing_schema(tmp_path: Path) -> None:
    path = tmp_path / "users.sqlite"
    path.touch()
    with pytest.raises(ProviderError, match="schema"):
        provider(path).read()
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("select name from sqlite_master").fetchall() == []


def test_t11_schema_mismatch_does_not_create_v2_storage(tmp_path: Path) -> None:
    path = tmp_path / "users.sqlite"
    v1 = SQLiteProvider(config=ProviderConfig(type=ProviderType.SQLITE, user_schema=1), path=path)
    v1.write(ProviderUsers(schema_version=1, users=[SFTPUser(username="alice", public_keys=[KEY])]))
    before = path.read_bytes()
    with pytest.raises(ProviderError, match="schema"):
        provider(path).read()
    assert path.read_bytes() == before
    assert v1.read().users[0].public_keys == [KEY]
