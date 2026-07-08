from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

import sftpwarden.utils.files as file_utils
from sftpwarden.config import ProviderConfig, ProviderType, default_project_config
from sftpwarden.providers import MariaDBProvider, MongoDBProvider, SQLiteProvider, provider_class
from sftpwarden.providers.mongodb_provider import mongodb_database_name
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError


def test_provider_registry_exposes_v11_providers() -> None:
    """Registry returns the public v1.1 provider classes."""
    assert provider_class("sqlite") is SQLiteProvider
    assert provider_class("mariadb") is MariaDBProvider
    assert provider_class("mongodb") is MongoDBProvider


def test_sqlite_provider_round_trip_and_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user_factory: Callable[..., SFTPUser],
) -> None:
    """SQLite stores and mutates users in a local private database file."""
    db_path = tmp_path / "users.sqlite"
    chmods: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        file_utils.os,
        "chmod",
        lambda chmod_path, mode: chmods.append((Path(chmod_path), mode)),
    )
    provider = SQLiteProvider(
        config=ProviderConfig(type=ProviderType.SQLITE, path="/etc/sftpwarden/users.sqlite"),
        path=db_path,
    )

    assert SQLiteProvider.empty_text() == ""
    assert not provider.table_exists()

    provider.create_table()
    provider.write(ProviderUsers(users=[user_factory("alice"), user_factory("bob")]))
    provider.upsert_user(user_factory("alice", comment="updated"))
    provider.remove_user("bob")
    loaded = provider.read()

    assert provider.table_exists()
    assert [loaded_user.username for loaded_user in loaded.users] == ["alice"]
    assert loaded.users[0].comment == "updated"
    assert chmods and all(call == (db_path, 0o600) for call in chmods)
    with pytest.raises(ProviderError, match="Unknown user"):
        provider.remove_user("missing")


def test_sqlite_provider_table_exists_requires_key_table_for_schema_v2(tmp_path: Path) -> None:
    """SQLite schema v2 storage is incomplete without the keys table."""
    db_path = tmp_path / "users.sqlite"
    provider = SQLiteProvider(
        config=ProviderConfig(
            type=ProviderType.SQLITE,
            path="/etc/sftpwarden/users.sqlite",
            user_schema=2,
        ),
        path=db_path,
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute("create table sftp_users (username text primary key)")

    assert not provider.table_exists()

    provider.create_table()

    assert provider.table_exists()


def test_sqlite_provider_schema_v2_mutations_manage_key_table(tmp_path: Path) -> None:
    """SQLite schema v2 writes, detects, and removes named key rows."""
    db_path = tmp_path / "users.sqlite"
    provider = SQLiteProvider(
        config=ProviderConfig(
            type=ProviderType.SQLITE,
            path="/etc/sftpwarden/users.sqlite",
            user_schema=2,
        ),
        path=db_path,
    )
    user = SFTPUser(
        username="alice",
        keys=[
            SFTPUserKey(
                name="prod",
                public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests",
            )
        ],
    )

    assert not provider.table_exists()
    provider.ensure_schema_storage(2)
    assert provider.table_exists()
    provider.write(ProviderUsers(schema_version=2, users=[user]))

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("select count(*) from sftp_user_keys").fetchone()[0]

    assert count == 1

    provider.remove_user("alice")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("select count(*) from sftp_user_keys").fetchone()[0]

    assert count == 0


def test_sqlite_provider_table_exists_false_when_file_has_no_user_table(tmp_path: Path) -> None:
    db_path = tmp_path / "users.sqlite"
    db_path.touch()
    provider = SQLiteProvider(
        config=ProviderConfig(type=ProviderType.SQLITE, path="/etc/sftpwarden/users.sqlite"),
        path=db_path,
    )

    assert not provider.table_exists()


def test_mongodb_provider_round_trip_and_delete_all(
    install_fake_pymongo: Callable,
    user_factory: Callable[..., SFTPUser],
) -> None:
    """MongoDB stores users by username and can replace the collection contents."""
    install_fake_pymongo()
    provider = MongoDBProvider(
        config=ProviderConfig(
            type=ProviderType.MONGODB,
            dsn="mongodb://localhost:27017/sftpwarden",
            collection="sftp_users",
        )
    )

    assert MongoDBProvider.empty_text() == ""
    assert not provider.table_exists()

    provider.create_table()
    provider.write(ProviderUsers(users=[user_factory("alice"), user_factory("bob")]))
    provider.upsert_user(user_factory("alice", comment="updated"))
    provider.remove_user("bob")
    loaded = provider.read()

    assert provider.table_exists()
    assert [loaded_user.username for loaded_user in loaded.users] == ["alice"]
    assert loaded.users[0].comment == "updated"
    with pytest.raises(ProviderError, match="Unknown user"):
        provider.remove_user("missing")

    provider.write(ProviderUsers(users=[]))
    assert provider.read().users == []


def test_mongodb_provider_preserves_document_schema_version_for_password_only_v2(
    install_fake_pymongo: Callable,
    test_password_hash: str,
) -> None:
    """MongoDB keeps schema v2 even before a user has named keys."""
    install_fake_pymongo()
    provider = MongoDBProvider(
        config=ProviderConfig(
            type=ProviderType.MONGODB,
            dsn="mongodb://localhost:27017/sftpwarden",
            collection="sftp_users",
            user_schema=2,
        )
    )
    user = SFTPUser(username="alice", password_hash=test_password_hash)

    provider.create_table()
    provider.upsert_user(user)
    loaded = provider.read()

    assert loaded.schema_version == 2
    assert loaded.users[0].username == "alice"
    assert loaded.users[0].keys == []

    provider.upsert_user(
        SFTPUser(
            username="alice",
            keys=[
                SFTPUserKey(
                    name="prod",
                    public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests",
                )
            ],
        )
    )

    loaded_with_key = provider.read()

    assert loaded_with_key.schema_version == 2
    assert loaded_with_key.users[0].keys[0].name == "prod"


def test_mongodb_dsn_and_dependency_errors(
    monkeypatch: pytest.MonkeyPatch,
    install_fake_pymongo: Callable,
) -> None:
    """MongoDB reports useful errors for missing DSNs, missing extras, and bad URLs."""
    assert mongodb_database_name("mongodb+srv://cluster.example.com/sftp") == "sftp"
    with pytest.raises(ProviderError, match="database name"):
        mongodb_database_name("mongodb://localhost:27017")

    provider = MongoDBProvider(
        config=ProviderConfig.model_construct(type=ProviderType.MONGODB, dsn=None)
    )
    with pytest.raises(ProviderError, match="requires dsn"):
        provider.read()

    monkeypatch.setitem(sys.modules, "pymongo", None)
    provider = MongoDBProvider(
        config=ProviderConfig(type=ProviderType.MONGODB, dsn="mongodb://localhost:27017/sftp")
    )
    with pytest.raises(ProviderError, match="optional dependency"):
        provider.read()

    monkeypatch.delitem(sys.modules, "pymongo", raising=False)
    install_fake_pymongo()
    with pytest.raises(ProviderError, match="mongodb://"):
        MongoDBProvider(
            config=ProviderConfig(type=ProviderType.MONGODB, dsn="http://localhost/sftp")
        ).read()


def test_v11_provider_config_defaults_and_validation() -> None:
    """Provider config validates storage-specific fields for v1.1 providers."""
    sqlite_config = default_project_config("dev", ProviderType.SQLITE)
    mongodb_config = default_project_config(
        "dev",
        ProviderType.MONGODB,
        dsn="mongodb://localhost:27017/sftp",
        collection="accounts",
    )

    assert sqlite_config.provider.path.endswith("users.sqlite")
    assert mongodb_config.provider.collection == "accounts"
    with pytest.raises(ValidationError, match="mongodb provider requires dsn"):
        ProviderConfig(type=ProviderType.MONGODB)
    with pytest.raises(ValidationError, match="query/table"):
        ProviderConfig(
            type=ProviderType.MONGODB,
            dsn="mongodb://localhost:27017/sftp",
            table="custom_users",
        )
    with pytest.raises(ValidationError, match="collection"):
        ProviderConfig(type=ProviderType.MYSQL, dsn="mysql://db/sftp", collection="users")
    with pytest.raises(ValidationError, match="sqlite"):
        ProviderConfig(type=ProviderType.SQLITE, dsn="sqlite:///users.sqlite")
