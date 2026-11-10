from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sftpwarden.config import load_config
from sftpwarden.providers import provider_for_config
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError
from tests.providers.test_concurrency import run_service_writers

from .conftest import CleanupStack, ReleaseCli, assert_ok, eventually, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
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
CASES = [(case, False) for case in DATABASE_CASES] + [(DATABASE_CASES[-1], True)]


@pytest.mark.parametrize(
    ("case", "replica"),
    CASES,
    ids=[case.provider + ("-replica" if replica else "") for case, replica in CASES],
)
@pytest.mark.parametrize("schema", [1, 2])
def test_t08_t10_database_concurrency_and_unrelated_key_access(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
    policy_image: str,
    case: DatabaseCase,
    replica: bool,
    schema: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = f"sftpwarden-cas-{unique_name}"
    network = f"sftpwarden-cas-net-{unique_name}"
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network], timeout=30))
    run_external(["docker", "network", "create", network], timeout=30)
    port = start_database_container(
        case,
        name=name,
        host_port=0,
        cleanup_stack=cleanup_stack,
        arguments=("--replSet", "rs0", "--bind_ip_all") if replica else (),
    )
    run_external(["docker", "network", "connect", network, name], timeout=30)
    if replica:

        def initialize_replica() -> bool:
            result = run_external(
                [
                    "docker",
                    "exec",
                    name,
                    "mongosh",
                    "--quiet",
                    "--eval",
                    "try { rs.status(); } catch (e) { "
                    "rs.initiate({_id:'rs0',members:[{_id:0,host:'localhost:27017'}]}); }",
                ],
                check=False,
                timeout=15,
            )
            return result.returncode == 0

        eventually(
            initialize_replica,
            timeout_seconds=90,
            description="test MongoDB replica initialization",
        )
    dsn = case.dsn_template.format(port=port) + ("?directConnection=true" if replica else "")
    project = tmp_path / "project"
    assert_ok(
        init_when_database_is_ready(cli, case=case, root=project, dsn=dsn, user_schema=schema)
    )
    config_path = project / "sftpwarden.yaml"
    config = load_config(config_path)
    provider = provider_for_config(config.provider)
    runtime_root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(runtime_root))
    runtime_root.mkdir(mode=0o700)
    server = PolicyRuntime(runtime_root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    users = [
        SFTPUser(username=name, keys=[SFTPUserKey(name=name, public_key=key)])
        if schema == 2
        else SFTPUser(username=name, public_keys=[key])
        for name, key in [("alice", server.alice_public), ("control", server.control_public)]
    ]
    for user in users:
        provider.mutate_user(provider.read(), user.username, user)
    runtime_config_path = server.inputs / "config.yaml"
    runtime_config = yaml.safe_load(runtime_config_path.read_text())
    runtime_config["provider"] = config.provider.model_dump(mode="json")
    runtime_config["provider"]["dsn"] = dsn.replace(
        f"127.0.0.1:{port}", f"{name}:{case.internal_port}"
    )
    runtime_config_path.write_text(yaml.safe_dump(runtime_config))
    server.start(docker_args=("--network", network))
    assert server.connect("alice").returncode == 0
    provider.upsert_user(users[0].model_copy(update={"comment": "single-user upsert"}))
    fresh = provider_for_config(config.provider).read()
    assert (
        next(user for user in fresh.users if user.username == "control").key_objects()
        == users[1].key_objects()
    )
    run_external(
        [
            "docker",
            "exec",
            server.name,
            "sftpwarden",
            "runtime",
            "refresh",
            "--config",
            "/inputs/config.yaml",
        ],
        timeout=40,
    )
    assert server.connect("control").returncode == 0

    assert run_service_writers(
        config_path, [("alice", "first edit"), ("control", "second edit")]
    ) == [0, 0]
    assert {user.username: user.comment for user in provider.read().users} == {
        "alice": "first edit",
        "control": "second edit",
    }
    assert run_service_writers(config_path, [("alice", "winner one"), ("alice", "winner two")]) == [
        0,
        3,
    ]
    assert server.connect("control").returncode == 0
    before = provider.read()
    if case.provider == "mongodb" and not replica:
        with pytest.raises(ProviderError, match="requires a replica set"):
            provider.replace_if_unchanged(before, ProviderUsers(schema_version=schema, users=[]))
        assert provider.read() == before
    else:
        provider.replace_if_unchanged(before, before)
        assert provider.read() == before
    stale = provider.read()
    changed = next(user for user in stale.users if user.username == "control").model_copy(
        update={"comment": "newer"}
    )
    provider.mutate_user(stale, "control", changed)
    with pytest.raises(ProviderError, match="conflict"):
        provider.mutate_user(stale, "control", None)
    if case.provider != "mongodb" or replica:
        with pytest.raises(ProviderError, match="conflict"):
            provider.replace_if_unchanged(stale, ProviderUsers(schema_version=schema, users=[]))
        assert_failed_bulk_is_rolled_back(provider, case.provider, schema, monkeypatch)
    assert server.connect("control").returncode == 0


def assert_failed_bulk_is_rolled_back(
    provider, kind: str, schema: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail after a real first write and verify rollback through a fresh connection."""
    before = provider.read()
    if kind == "mongodb":
        database = provider._database()
        collection = database[provider.config.collection]

        class FailingCollection:
            calls = 0

            def replace_one(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise OSError("Injected interruption after first real MongoDB write")
                return collection.replace_one(*args, **kwargs)

        failing = FailingCollection()

        class Database:
            client = database.client

            def __getitem__(self, name):
                return failing

        changed = before.model_copy(deep=True)
        changed.users[0].comment = "must roll back"
        with monkeypatch.context() as patch:
            patch.setattr(provider, "_database", lambda: Database())
            with pytest.raises(ProviderError, match="bulk write failed"):
                provider.write(changed)
    elif schema == 2:
        changed = before.model_copy(deep=True)
        changed.users[1].keys = changed.users[0].keys
        with pytest.raises(ProviderError, match="Database provider operation failed"):
            provider.write(changed)
    assert provider_for_config(provider.config).read() == before
