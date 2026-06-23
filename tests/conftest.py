from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import ContextEntry, ContextRegistry, local_context, save_registry
from sftpwarden.render.compose import write_compose
from sftpwarden.users import ProviderUsers, SFTPUser

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


@pytest.fixture
def test_password_hash() -> str:
    """Return a stable password hash for user fixtures."""
    return TEST_HASH


@pytest.fixture
def user_factory(test_password_hash: str) -> Callable[..., SFTPUser]:
    """Return a factory for valid SFTP users."""

    def build(
        username: str = "alice",
        *,
        comment: str | None = None,
        public_keys: list[str] | None = None,
        uid: int | None = None,
        gid: int | None = None,
        disabled: bool = False,
    ) -> SFTPUser:
        return SFTPUser(
            username=username,
            password_hash=test_password_hash,
            public_keys=public_keys or [],
            uid=uid,
            gid=gid,
            comment=comment,
            disabled=disabled,
        )

    return build


class MemoryProvider:
    """Mutable in-memory provider used by provider-transfer tests."""

    def __init__(self, users: ProviderUsers) -> None:
        self.users = users
        self.writes: list[ProviderUsers] = []

    def read(self) -> ProviderUsers:
        """Return the current provider users."""
        return self.users

    def write(self, users: ProviderUsers) -> None:
        """Replace current provider users."""
        self.users = users
        self.writes.append(users)


@pytest.fixture
def memory_provider_factory() -> Callable[[ProviderUsers], MemoryProvider]:
    """Return a factory for mutable in-memory providers."""
    return MemoryProvider


@pytest.fixture
def local_project_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[Path, ContextEntry]]:
    """Return a factory that creates and registers a small local project."""

    def build(
        *,
        name: str = "dev",
        provider: ProviderType = ProviderType.YAML,
        root: Path | None = None,
    ) -> tuple[Path, ContextEntry]:
        monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
        project_root = root or tmp_path / name / "project"
        project_root.mkdir(parents=True, exist_ok=True)
        config = default_project_config(name, provider)
        write_config(project_root / "sftpwarden.yaml", config)
        if provider == ProviderType.YAML:
            (project_root / "users.yaml").write_text("users: []\n", encoding="utf-8")
        elif provider == ProviderType.CSV:
            (project_root / "users.csv").write_text(
                "username,public_keys,password_hash,uid,gid,upload_dir,comment,disabled\n",
                encoding="utf-8",
            )
        write_compose(config, project_root)
        entry = local_context(name, project_root, provider)
        save_registry(ContextRegistry(default=name, contexts={name: entry}))
        return project_root, entry

    return build


class FakeMongoCursor(list[dict[str, Any]]):
    """Tiny MongoDB cursor fake supporting sort."""

    def sort(self, key: str, direction: int) -> FakeMongoCursor:  # type: ignore
        """Return documents sorted like a PyMongo cursor."""
        reverse = direction < 0
        return FakeMongoCursor(sorted(self, key=lambda document: document[key], reverse=reverse))


class FakeDeleteResult:
    """Tiny MongoDB delete result fake."""

    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeMongoCollection:
    """In-memory collection fake for MongoDB provider tests."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.indexes: list[tuple[str, bool]] = []

    def create_index(self, key: str, *, unique: bool) -> None:
        """Record requested indexes."""
        self.indexes.append((key, unique))

    def find(self, _query: dict, projection: dict[str, bool]) -> FakeMongoCursor:
        """Return all visible documents."""
        documents = []
        for document in self.documents.values():
            visible = dict(document)
            if "_id" in projection and not projection["_id"]:
                visible.pop("_id", None)
            documents.append(visible)
        return FakeMongoCursor(documents)

    def replace_one(self, query: dict, document: dict[str, Any], *, upsert: bool) -> None:
        """Replace one document by id."""
        if not upsert and query["_id"] not in self.documents:
            return
        self.documents[query["_id"]] = dict(document)

    def delete_many(self, query: dict) -> None:
        """Delete many documents."""
        if not query:
            self.documents.clear()
            return
        allowed = set(query["_id"]["$nin"])
        for key in list(self.documents):
            if key not in allowed:
                del self.documents[key]

    def delete_one(self, query: dict) -> FakeDeleteResult:
        """Delete one document by id."""
        deleted = int(query["_id"] in self.documents)
        self.documents.pop(query["_id"], None)
        return FakeDeleteResult(deleted)


class FakeMongoDatabase:
    """In-memory MongoDB database fake."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeMongoCollection] = {}

    def __getitem__(self, name: str) -> FakeMongoCollection:
        """Return a collection by name."""
        return self.collections.setdefault(name, FakeMongoCollection())

    def list_collection_names(self) -> list[str]:
        """Return existing collection names."""
        return list(self.collections)

    def create_collection(self, name: str) -> None:
        """Create a collection if needed."""
        self.collections.setdefault(name, FakeMongoCollection())


@pytest.fixture
def install_fake_pymongo(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], dict[tuple[str, str], FakeMongoDatabase]]:
    """Return a helper that installs a fake pymongo module."""

    def install() -> dict[tuple[str, str], FakeMongoDatabase]:
        databases: dict[tuple[str, str], FakeMongoDatabase] = {}
        pymongo = types.ModuleType("pymongo")

        class FakeMongoClient:
            def __init__(self, dsn: str) -> None:
                self.dsn = dsn

            def __getitem__(self, name: str) -> FakeMongoDatabase:
                return databases.setdefault((self.dsn, name), FakeMongoDatabase())

        pymongo.MongoClient = FakeMongoClient  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pymongo", pymongo)
        return databases

    return install
