from __future__ import annotations

import os
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sftpwarden.config import ProviderConfig, ProviderType, default_project_config, write_config
from sftpwarden.contexts import ContextEntry, ContextRegistry, local_context, save_registry
from sftpwarden.providers import empty_provider_text
from sftpwarden.render.compose import write_compose
from sftpwarden.users import ProviderUsers, SFTPUser

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"

RELEASE_VALIDATION_DIR = Path(__file__).parent / "release_validation"


@pytest.fixture(autouse=True)
def isolated_upgrade_inspection(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Keep the new deployment inspection boundary off real infrastructure in fast tests.

    Upgrade contract tests override these responses for failure and legacy cases;
    opt-in release tests always execute the real probes.
    """
    if Path(str(request.node.path)).is_relative_to(RELEASE_VALIDATION_DIR):
        return
    from sftpwarden.services import deployment_upgrade
    from sftpwarden.system.commands import CommandResult
    from sftpwarden.utils._version import get_version

    def pin(config):
        selected = config.model_copy(deep=True)
        selected.kubernetes.kube_context = selected.kubernetes.kube_context or "test-cluster"
        return selected

    monkeypatch.setattr(deployment_upgrade, "pin_context", pin)

    def inspect(command, **_kwargs):
        parts = command if command[0] != "ssh" else command[-1].split()
        if command[0] == "ssh" and command[-1].startswith("head -c "):
            output = default_project_config("remote-test").model_dump_json()
        elif "exec" in parts:
            output = get_version()
        elif "--quiet" in parts or "--ignore-not-found" in parts:
            output = "test-runtime"
        else:
            raise AssertionError(f"Unexpected external upgrade probe: {command}")
        return CommandResult(command, 0, output, "")

    monkeypatch.setattr(deployment_upgrade, "run", inspect)


@pytest.fixture
def applied_runtime_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise applied snapshots in private temporary paths without host sshd mutation."""
    from sftpwarden.runtime import configuration

    monkeypatch.setattr(configuration, "APPLIED_CONFIG_PATH", tmp_path / "applied-config.json")
    monkeypatch.setattr(configuration, "SSHD_CONFIG_PATH", tmp_path / "applied-sshd-config")
    monkeypatch.setattr(configuration, "validate_sshd_candidate", lambda _path: None)
    return configuration.prepare_runtime_configuration


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register opt-in release validation controls."""
    parser.addoption(
        "--run-release-validation",
        action="store_true",
        default=False,
        help="Run the explicit SFTPWarden release validation suite.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Document release validation markers for explicit runs."""
    if os.environ.get("SFTPWARDEN_TEST_INSTALLED_ARTIFACT") == "1":
        import sftpwarden

        source_root = Path(__file__).resolve().parents[1]
        package_path = Path(sftpwarden.__file__).resolve()
        if package_path.is_relative_to(source_root):
            raise pytest.UsageError(
                "Artifact validation imported the checkout instead of the installed candidate."
            )
    config.addinivalue_line(
        "markers",
        "release_validation: opt-in end-to-end release validation tests",
    )
    config.addinivalue_line(
        "markers",
        "release_external: release validation tests that require external tools/services",
    )
    config.addinivalue_line(
        "markers",
        "release_docker: release validation tests that require Docker and Docker Compose",
    )
    config.addinivalue_line(
        "markers",
        "release_kubernetes: release validation tests that require kubectl and a cluster",
    )
    config.addinivalue_line(
        "markers",
        "release_helm: release validation tests that require Helm and a cluster",
    )
    config.addinivalue_line(
        "markers",
        "release_databases: release validation tests that start provider databases in Docker",
    )
    config.addinivalue_line(
        "markers",
        "release_systemd: native Linux systemd lifecycle in an explicitly disposable runner",
    )
    config.addinivalue_line(
        "markers",
        "release_upgrade: upgrade from the published amd64 1.3.0 runtime",
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:
    """Keep release validation out of tox and normal pytest runs."""
    release_requested = config.getoption("--run-release-validation") or (
        os.environ.get("SFTPWARDEN_RUN_RELEASE_VALIDATION") == "1"
    )
    path = Path(str(collection_path))
    try:
        path.relative_to(RELEASE_VALIDATION_DIR)
    except ValueError:
        return False
    return not release_requested


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
        self.config = ProviderConfig(type=ProviderType.YAML, user_schema=users.schema_version)
        self.path = None
        self.users = users
        self.writes: list[ProviderUsers] = []

    def read(self) -> ProviderUsers:
        """Return the current provider users."""
        return self.users

    def write(self, users: ProviderUsers) -> None:
        """Replace current provider users."""
        self.users = users
        self.writes.append(users)

    def replace_if_unchanged(self, expected: ProviderUsers, replacement: ProviderUsers) -> None:
        """Model checked replacement for workflow tests without a storage backend."""
        from sftpwarden.providers.mutations import require_unchanged_snapshot

        require_unchanged_snapshot(self.users, expected)
        self.write(replacement)


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
        if provider in {ProviderType.YAML, ProviderType.CSV}:
            provider_path = project_root / (
                "users.csv" if provider == ProviderType.CSV else "users.yaml"
            )
            provider_path.write_text(
                empty_provider_text(provider, user_schema=config.provider.user_schema),
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

    def close(self) -> None:
        """Match the explicit metadata-cursor cleanup contract."""


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

    def find(self, _query: dict, projection: dict[str, bool], **_kwargs: Any) -> FakeMongoCursor:
        """Return all visible documents."""
        documents = []
        for document in self.documents.values():
            visible = dict(document)
            if "_id" in projection and not projection["_id"]:
                visible.pop("_id", None)
            documents.append(visible)
        return FakeMongoCursor(documents)

    def find_one(self, query: dict) -> dict[str, Any] | None:
        """Return a detached document for comparison."""
        document = self.documents.get(query["_id"])
        return dict(document) if document is not None else None

    def insert_one(self, document: dict[str, Any]) -> None:
        """Reject duplicate document identities."""
        if document["_id"] in self.documents:
            raise ValueError("Duplicate test document")
        self.documents[document["_id"]] = dict(document)

    def _matches(self, query: dict) -> bool:
        document = self.documents.get(query["_id"])
        return document is not None and (
            "$expr" not in query or query["$expr"]["$eq"][1]["$literal"] == document
        )

    def replace_one(
        self, query: dict, document: dict[str, Any], *, upsert: bool = False, **_kwargs: Any
    ) -> Any:
        """Replace one document by id."""
        if not upsert and not self._matches(query):
            return types.SimpleNamespace(matched_count=0)
        self.documents[query["_id"]] = dict(document)
        return types.SimpleNamespace(matched_count=1)

    def delete_many(self, query: dict, **_kwargs: Any) -> None:
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
        deleted = int(self._matches(query))
        if deleted:
            self.documents.pop(query["_id"], None)
        return FakeDeleteResult(deleted)


class FakeMongoDatabase:
    """In-memory MongoDB database fake."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeMongoCollection] = {}
        self.client: Any = None

    def __getitem__(self, name: str) -> FakeMongoCollection:
        """Return a collection by name."""
        return self.collections.setdefault(name, FakeMongoCollection())

    def list_collection_names(self) -> list[str]:
        """Return existing collection names."""
        return list(self.collections)

    def list_collections(self, *, filter: dict[str, str]) -> FakeMongoCursor:
        return FakeMongoCursor(
            [
                {"name": name, "info": {"uuid": id(collection)}}
                for name, collection in self.collections.items()
                if name == filter["name"]
            ]
        )

    def create_collection(self, name: str) -> None:
        """Create a collection if needed."""
        self.collections.setdefault(name, FakeMongoCollection())


@pytest.fixture
def install_fake_pymongo(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], dict[tuple[str, str], FakeMongoDatabase]]:
    """Return a helper that installs a fake pymongo module."""

    def install(*, replica_set: bool = True) -> dict[tuple[str, str], FakeMongoDatabase]:
        databases: dict[tuple[str, str], FakeMongoDatabase] = {}
        pymongo = types.ModuleType("pymongo")

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start_transaction(self):
                from contextlib import nullcontext

                return nullcontext()

        class FakeMongoClient:
            def __init__(self, dsn: str, **kwargs: Any) -> None:
                self.dsn = dsn
                self.options = kwargs
                self.admin = types.SimpleNamespace(
                    command=lambda _command: {"setName": "test"} if replica_set else {}
                )

            def __getitem__(self, name: str) -> FakeMongoDatabase:
                database = databases.setdefault((self.dsn, name), FakeMongoDatabase())
                database.client = self
                return database

            def start_session(self):
                return Session()

            def close(self):
                pass

        pymongo.MongoClient = FakeMongoClient  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pymongo", pymongo)
        return databases

    return install
