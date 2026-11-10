from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from sftpwarden.config import (
    ProviderType,
    default_project_config,
    provider_local_path,
    write_config,
)
from sftpwarden.providers import provider_for_config
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError

WORKER = """
import os, sys
from pathlib import Path
import sftpwarden
if baseline := os.environ.get('SFTPWARDEN_TEST_BASELINE_CHECKOUT'):
    assert Path(sftpwarden.__file__).resolve().is_relative_to(Path(baseline).resolve())
from sftpwarden.services.users import UserService
from sftpwarden.utils.errors import ProviderError
service = UserService(config_path=sys.argv[1])
original = service.provider.read
first = True
def barrier():
    global first
    result = original()
    if first:
        first = False
        print('ready', flush=True)
        assert sys.stdin.readline().strip() == 'go'
    return result
service.provider.read = barrier
try:
    service.update_user(sys.argv[2], comment=sys.argv[3])
except ProviderError as error:
    if 'conflict' not in str(error).lower():
        raise
    sys.exit(3)
"""


def run_service_writers(config_path: Path, changes: list[tuple[str, str]]) -> list[int]:
    """Release independent service processes after all have read their snapshots."""
    workers: list[subprocess.Popen[str]] = []
    ready: queue.Queue[str] = queue.Queue()
    environment = os.environ.copy()
    checkout = environment.get("SFTPWARDEN_TEST_BASELINE_CHECKOUT")
    if checkout:
        environment["PYTHONPATH"] = checkout

    def report_ready(process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        ready.put(process.stdout.readline().strip())

    try:
        for username, comment in changes:
            process = subprocess.Popen(
                [sys.executable, "-c", WORKER, str(config_path), username, comment],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                cwd=checkout,
            )
            workers.append(process)
            threading.Thread(target=report_ready, args=(process,), daemon=True).start()
        for _ in workers:
            assert ready.get(timeout=15) == "ready", "Writer failed before the concurrency barrier"
        for process in workers:
            assert process.stdin is not None
            process.stdin.write("go\n")
            process.stdin.flush()
        for process in workers:
            process.communicate(timeout=30)
        return sorted(process.returncode for process in workers)
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
@pytest.mark.parametrize("same_user", [False, True])
def test_t08_independent_service_writers(
    tmp_path: Path, kind: ProviderType, same_user: bool
) -> None:
    config = default_project_config("concurrency", kind)
    config_path = tmp_path / "sftpwarden.yaml"
    write_config(config_path, config)
    provider = provider_for_config(config.provider, provider_local_path(tmp_path, config))
    provider.write(
        ProviderUsers(
            schema_version=2, users=[SFTPUser(username="alice"), SFTPUser(username="bob")]
        )
    )
    statuses = run_service_writers(
        config_path, [("alice", "first edit"), ("alice" if same_user else "bob", "second edit")]
    )
    if same_user:
        assert statuses == [0, 3], "One stale same-user write must report a conflict"
    else:
        assert statuses == [0, 0]
        actual = {user.username: user.comment for user in provider.read().users}
        assert actual == {"alice": "first edit", "bob": "second edit"}


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_checked_replacement_rejects_stale_metadata_and_delete(
    tmp_path: Path, kind: ProviderType
) -> None:
    config = default_project_config("checked", kind)
    provider = provider_for_config(config.provider, provider_local_path(tmp_path, config))
    provider.write(ProviderUsers(schema_version=2, users=[SFTPUser(username="alice")]))
    stale = provider.read()
    changed = stale.users[0].model_copy(update={"comment": "new metadata"})
    provider.mutate_user(stale, "alice", changed)
    current = provider.read()
    with pytest.raises(ProviderError, match="conflict"):
        provider.replace_if_unchanged(stale, ProviderUsers(schema_version=2, users=[]))
    with pytest.raises(ProviderError, match="conflict"):
        provider.mutate_user(stale, "alice", None)
    assert provider.read() == current
    provider.mutate_user(current, "alice", None)
    assert provider.read().users == []


def test_standalone_mongodb_rejects_bulk_before_any_mutation(install_fake_pymongo) -> None:
    from sftpwarden.config import ProviderConfig
    from sftpwarden.providers import MongoDBProvider

    databases = install_fake_pymongo(replica_set=False)
    provider = MongoDBProvider(
        ProviderConfig(type=ProviderType.MONGODB, dsn="mongodb://localhost/test")
    )
    with pytest.raises(ProviderError, match="requires a replica set"):
        provider.write(ProviderUsers(users=[SFTPUser(username="alice")]))
    assert not databases[("mongodb://localhost/test", "test")].collections
    provider.mutate_user(ProviderUsers(users=[]), "alice", SFTPUser(username="alice"))
    assert provider.read().users[0].username == "alice"
