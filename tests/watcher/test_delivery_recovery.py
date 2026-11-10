"""Pending delivery survives failures and restarts without relying on file mtimes."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, remote_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.utils.errors import ContextError
from sftpwarden.watcher import core, delivery, polling
from sftpwarden.watcher.delivery import read_snapshot


@pytest.mark.parametrize("platform,shared", [("posix", "1"), ("nt", "1"), ("posix", "0")])
def test_shared_checkpoint_uses_host_directory_ownership(tmp_path, monkeypatch, platform, shared):
    path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(
        polling,
        "os",
        SimpleNamespace(name=platform, environ={"SFTPWARDEN_SHARED_CHECKPOINTS": shared}),
    )
    writes = []
    monkeypatch.setattr(polling, "atomic_bytes", lambda *args, **kwargs: writes.append(kwargs))
    polling.save_checkpoint(path, polling.DeliveryState())
    expected = (tmp_path.stat().st_uid, tmp_path.stat().st_gid)
    assert writes == [{"owner": expected if platform == "posix" and shared == "1" else None}]


def test_shared_checkpoint_rejects_redirected_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        polling,
        "os",
        SimpleNamespace(name="posix", environ={"SFTPWARDEN_SHARED_CHECKPOINTS": "1"}),
    )
    # Exercise the rejection without requiring Windows symlink privileges.
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == tmp_path)
    with pytest.raises(OSError, match="symbolic link"):
        polling.checkpoint_owner(tmp_path / "checkpoint.json")


@pytest.fixture
def watched_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    contexts = {}
    items = {}
    for name in ("broken", "healthy"):
        root = tmp_path / name
        root.mkdir()
        config = default_project_config(name)
        config.provider.path = f"{CONTAINER_PROVIDER_DATA_DIR}/users.yaml"
        write_config(root / "sftpwarden.yaml", config)
        provider = provider_from_config(root, config)
        provider.write(
            ProviderUsers(
                schema_version=2,
                users=[
                    SFTPUser(username="alice", disabled=True, comment="first"),
                ],
            )
        )
        entry = remote_context(
            name=name,
            provider=config.provider.type,
            remote_url=f"deploy@{name}:/srv/sftp",
            local_root=root,
            remote_root="/srv/sftp",
            remote_only=False,
            ssh_key=None,
            critical=False,
        )
        contexts[name] = entry
        items[name] = (entry, config, provider)
    save_registry(ContextRegistry(default="healthy", contexts=contexts))
    now = [0.0]
    monkeypatch.setattr(polling.time, "monotonic", lambda: now[0])
    calls = []
    monkeypatch.setattr(
        polling,
        "deliver_snapshot",
        lambda entry, *args, **kwargs: calls.append(("deliver", entry.name)),
    )
    monkeypatch.setattr(
        polling, "refresh_context", lambda entry, **kwargs: calls.append(("refresh", entry.name))
    )
    return items, now, calls


def advance(poller, now, count=1):
    for _ in range(count):
        poller.poll()
        now[0] += 10


def checkpoint(item):
    entry, _config, provider = item
    return polling.load_checkpoint(polling.checkpoint_path(entry, provider.path))


def test_checkpoint_survives_container_path_rewrite_but_not_retargeting(watched_projects, tmp_path):
    items, _now, _calls = watched_projects
    entry, _config, provider = items["healthy"]
    original = polling.checkpoint_path(entry, provider.path)
    container = entry.model_copy(deep=True)
    container.root = str(tmp_path / "container-workspace")
    container.remote.ssh_key = "/private/container-key"
    container_path = Path(container.root) / provider.path.relative_to(Path(entry.root))
    assert polling.checkpoint_path(container, container_path) == original
    container.remote.remote_root = "/srv/other-project"
    assert polling.checkpoint_path(container, container_path) != original
    assert polling.checkpoint_path(entry, provider.path.with_name("other.yaml")) != original


def test_failed_host_does_not_block_others_and_retries_without_edit(watched_projects, monkeypatch):
    items, now, calls = watched_projects
    failing = [True]

    def deliver(entry, *args, **kwargs):
        calls.append(("deliver", entry.name))
        if entry.name == "broken" and failing[0]:
            raise ContextError("synthetic failure")

    monkeypatch.setattr(polling, "deliver_snapshot", deliver)
    poller = polling.WatchPoller()
    advance(poller, now, 2)
    assert checkpoint(items["healthy"]).phase == "applied"
    assert not checkpoint(items["broken"]).applied
    failing[0] = False
    advance(poller, now)
    assert checkpoint(items["broken"]).phase == "applied"
    assert calls.count(("refresh", "healthy")) == 1


def test_restart_retries_a_delivered_version_whose_refresh_failed(watched_projects, monkeypatch):
    items, now, calls = watched_projects

    def fail(entry, **kwargs):
        raise ContextError("refresh failed")

    monkeypatch.setattr(polling, "refresh_context", fail)
    advance(polling.WatchPoller(), now, 2)
    state = checkpoint(items["healthy"])
    assert state.delivered and not state.applied and state.refresh_pending
    monkeypatch.setattr(
        polling, "refresh_context", lambda entry, **kwargs: calls.append(("refresh", entry.name))
    )
    advance(polling.WatchPoller(), now, 2)
    assert checkpoint(items["healthy"]).phase == "applied"
    assert not checkpoint(items["healthy"]).refresh_pending


def test_equal_mtime_comment_edit_is_delivered_without_refresh(watched_projects):
    items, now, calls = watched_projects
    poller = polling.WatchPoller()
    advance(poller, now, 2)
    entry, config, provider = items["healthy"]
    before = provider.path.stat()
    content = provider.path.read_bytes().replace(b"first", b"later")
    provider.path.write_bytes(content)
    os.utime(provider.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    advance(poller, now, 2)
    assert calls.count(("deliver", "healthy")) == 2
    assert calls.count(("refresh", "healthy")) == 1
    assert checkpoint(items["healthy"]).applied == read_snapshot(entry, config).digest


def test_invalid_and_missing_input_preserve_last_acknowledged_state(watched_projects):
    items, now, calls = watched_projects
    poller = polling.WatchPoller()
    advance(poller, now, 2)
    _entry, _config, provider = items["healthy"]
    content = provider.path.read_bytes()
    applied = checkpoint(items["healthy"]).applied
    provider.path.write_bytes(b"users: [unfinished")
    advance(poller, now)
    assert checkpoint(items["healthy"]).applied == applied
    assert checkpoint(items["healthy"]).phase == "input_failed"
    provider.path.unlink()
    advance(poller, now)
    assert checkpoint(items["healthy"]).applied == applied
    provider.path.write_bytes(content)
    advance(poller, now, 2)
    assert checkpoint(items["healthy"]).phase == "applied"
    assert calls.count(("refresh", "healthy")) == 1


def test_config_only_edit_does_not_sync_or_refresh(watched_projects):
    items, now, calls = watched_projects
    poller = polling.WatchPoller()
    advance(poller, now, 2)
    before = list(calls)
    entry, config, _provider = items["healthy"]
    config.auth.allow_password = not config.auth.allow_password
    write_config(Path(entry.config), config)
    advance(poller, now, 3)
    assert calls == before


def test_dry_run_never_writes_delivery_checkpoints(watched_projects):
    items, now, _calls = watched_projects
    advance(polling.WatchPoller(dry_run=True), now, 3)
    entry, _config, provider = items["healthy"]
    path = polling.checkpoint_path(entry, provider.path)
    assert not path.parent.exists()


def test_reverting_after_failed_refresh_still_refreshes_old_policy(watched_projects, monkeypatch):
    items, now, calls = watched_projects
    poller = polling.WatchPoller()
    advance(poller, now, 2)
    entry, _config, provider = items["healthy"]
    old = provider.path.read_bytes()
    users = provider.read()
    users.users[0].uid = 2001
    provider.write(users)

    def fail(context, **kwargs):
        if context.name == entry.name:
            raise ContextError("failure after runtime effects")

    monkeypatch.setattr(polling, "refresh_context", fail)
    advance(poller, now, 2)
    assert checkpoint(items["healthy"]).refresh_pending
    provider.path.write_bytes(old)
    monkeypatch.setattr(
        polling,
        "refresh_context",
        lambda context, **kwargs: calls.append(("refresh", context.name)),
    )
    advance(poller, now, 2)
    assert calls.count(("refresh", "healthy")) == 2
    assert not checkpoint(items["healthy"]).refresh_pending


@pytest.mark.parametrize("problem", ["external", "directory", "size", "encoding", "schema"])
def test_snapshot_rejects_unsafe_inputs_before_delivery(watched_projects, monkeypatch, problem):
    items, _, _ = watched_projects
    entry, config, provider = items["healthy"]
    if problem == "external":
        config.provider.type = ProviderType.POSTGRESQL
    elif problem == "directory":
        provider.path.unlink()
        provider.path.mkdir()
    elif problem == "size":
        monkeypatch.setattr(delivery, "MAX_METADATA_BYTES", 1)
    elif problem == "encoding":
        provider.path.write_bytes(b"\xff")
    else:
        config.provider.user_schema = 1
    with pytest.raises(ContextError):
        read_snapshot(entry, config)


@pytest.mark.parametrize("oversize", [False, True])
def test_sqlite_snapshot_reads_a_consistent_copy_and_bounds_sidecars(
    local_project_factory, monkeypatch, oversize
):
    root, entry = local_project_factory(provider=ProviderType.SQLITE)
    from sftpwarden.config import load_config

    config = load_config(entry.config)
    provider = provider_from_config(root, config)
    provider.write(ProviderUsers(schema_version=config.provider.user_schema, users=[]))
    if oversize:
        provider.path.with_name(provider.path.name + "-wal").write_bytes(b"x" * 100)
        monkeypatch.setattr(delivery, "MAX_METADATA_BYTES", provider.path.stat().st_size + 1)
        with pytest.raises(ContextError, match="limit"):
            read_snapshot(entry, config)
    else:
        snapshot = read_snapshot(entry, config)
        assert snapshot.content.startswith(b"SQLite format 3")
        assert len(snapshot.digest) == len(snapshot.policy) == 64


@pytest.mark.parametrize(
    "problem", ["missing-endpoint", "destination", "legacy-layout", "wrong-directory"]
)
def test_delivery_refuses_inconsistent_destinations(watched_projects, monkeypatch, problem):
    items, _, _ = watched_projects
    entry, config, _ = items["healthy"]
    snapshot = read_snapshot(entry, config)
    destination = "/srv/sftp/provider/users.yaml"
    if problem == "missing-endpoint":
        entry.remote = None
    elif problem == "destination":
        destination = "/srv/other/users.yaml"
    elif problem == "legacy-layout":
        config.provider.path = "/etc/sftpwarden/users.yaml"
        destination = "/srv/sftp/users.yaml"
    else:
        monkeypatch.setattr(
            delivery, "provider_local_path", lambda root, cfg: Path(root) / "other/users.yaml"
        )
        destination = "/srv/sftp/other/users.yaml"
    with pytest.raises(ContextError):
        delivery.delivery_script(entry, config, destination, snapshot)


@pytest.mark.parametrize("outcome", ["exit", "timeout"])
def test_lost_delivery_reply_is_never_acknowledged(watched_projects, monkeypatch, outcome):
    items, _, _ = watched_projects
    entry, config, _ = items["healthy"]
    snapshot = read_snapshot(entry, config)

    def run(args, **kwargs):
        assert kwargs["input"] == snapshot.content
        assert kwargs["timeout"] == 60
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, 60)
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(delivery.subprocess, "run", run)
    with pytest.raises(ContextError):
        delivery.deliver_snapshot(entry, config, "/srv/sftp/provider/users.yaml", snapshot)


@pytest.mark.parametrize("damage", ["config", "missing-provider", "checkpoint"])
def test_watcher_status_reports_input_and_checkpoint_failures(
    watched_projects, monkeypatch, damage
):
    items, _, _ = watched_projects
    entry, _, provider = items["healthy"]
    if damage == "config":
        Path(entry.config).write_text("invalid: [")
    elif damage == "missing-provider":
        provider.path.unlink()
    else:

        def fail(path):
            raise OSError("private-sentinel")

        monkeypatch.setattr(core, "load_checkpoint", fail)
    data = core.watcher_status_data()
    row = next(item for item in data["delivery"] if item["context"] == entry.name)
    assert row["phase"] == ("unreadable" if damage == "checkpoint" else "input_failed")
    assert "private-sentinel" not in core.watcher_status_text()


def test_sync_target_rejects_a_different_local_provider(watched_projects):
    items, _, _ = watched_projects
    entry, _, provider = items["healthy"]
    with pytest.raises(ContextError, match="configured user provider"):
        core.sync_target(
            entry, provider.path.with_name("different.yaml"), "/srv/sftp/provider/users.yaml"
        )
