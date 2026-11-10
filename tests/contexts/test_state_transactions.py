from __future__ import annotations

import threading
from pathlib import Path

import pytest
import typer

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.config.global_config import GlobalConfig, save_global_config
from sftpwarden.contexts import (
    ContextRegistry,
    load_registry,
    local_context,
    reconcile_registered_context,
    reconcile_registered_paths,
    register_context,
    remote_context,
    save_registry,
)
from sftpwarden.services import context_cleanup
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils import state_transactions
from sftpwarden.utils.errors import ContextError, SFTPWardenError
from sftpwarden.watcher.core import install_watcher, uninstall_watcher


@pytest.mark.parametrize("kind", ["registry", "global"])
def test_state_publication_failure_preserves_previous_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    target = tmp_path / "state.toml"
    previous = b"previous complete document\n"
    target.write_bytes(previous)

    def fail(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr("sftpwarden.utils.persistence.os.replace", fail)
    with pytest.raises(OSError, match="publication failure"):
        if kind == "registry":
            save_registry(ContextRegistry(), target)
        else:
            save_global_config(GlobalConfig(), target)
    assert target.read_bytes() == previous
    assert {path.name for path in tmp_path.iterdir()} == {"state.toml", ".state.toml.lock"}


def test_state_lock_is_reentrant_but_does_not_skip_other_threads(tmp_path: Path) -> None:
    target = tmp_path / "state.toml"
    attempted = threading.Event()
    entered = threading.Event()

    def contender():
        attempted.set()
        with state_transactions.state_transaction(target):
            entered.set()

    with (
        state_transactions.state_transaction(target),
        state_transactions.state_transaction(target),
    ):
        worker = threading.Thread(target=contender)
        worker.start()
        assert attempted.wait(timeout=2)
        assert not entered.is_set()
    worker.join(timeout=6)
    assert not worker.is_alive()
    assert entered.is_set()


def test_state_lock_releases_on_failure_and_reports_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.toml"
    with (
        pytest.raises(ValueError, match="interrupted"),
        state_transactions.state_transaction(target),
    ):
        raise ValueError("interrupted")

    def timeout(_path, **kwargs):
        raise TimeoutError("private detail")

    monkeypatch.setattr(state_transactions, "file_lock", timeout)
    with (
        pytest.raises(SFTPWardenError, match="Timed out waiting for local state") as raised,
        state_transactions.state_transaction(target),
    ):
        pytest.fail("Failed transaction must not enter")
    assert "private detail" not in str(raised.value)


@pytest.mark.parametrize("reconcile", [reconcile_registered_context, reconcile_registered_paths])
def test_reconciliation_reads_fresh_registry_and_does_not_resurrect_deleted_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reconcile
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    write_config(root / "sftpwarden.yaml", default_project_config("one"))
    first = local_context("one", root, ProviderType.YAML)
    register_context(first)
    stale = load_registry()
    first.critical = True
    register_context(first)
    register_context(local_context("two", root, ProviderType.CSV))
    assert reconcile(stale, "one").critical
    assert set(load_registry().contexts) == {"one", "two"}
    save_registry(ContextRegistry())
    with pytest.raises(ContextError, match="Unknown context"):
        reconcile(stale, "one")
    assert not load_registry().contexts


def test_watcher_dry_run_does_not_create_state_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    assert "systemd" in install_watcher(mode="systemd", dry_run=True)
    assert "not installed" in uninstall_watcher(dry_run=True)
    assert not home.exists()


def test_rename_does_not_overwrite_another_context(tmp_path, monkeypatch) -> None:
    from sftpwarden.cli_commands.context import context_rename

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    register_context(local_context("one", tmp_path / "one", ProviderType.YAML))
    register_context(local_context("two", tmp_path / "two", ProviderType.CSV))
    before = load_registry()
    with pytest.raises(typer.Exit):
        context_rename("one", "two")
    assert load_registry() == before


def test_remote_probe_cannot_prune_an_entry_changed_during_ssh(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    entry = remote_context(
        name="remote",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/old",
        local_root=None,
        remote_root="/old",
        remote_only=True,
        ssh_key=None,
        critical=False,
    )
    register_context(entry)
    changed = entry.model_copy(deep=True)
    changed.remote.remote_root = "/new"
    changed.remote.remote_config = "/new/sftpwarden.yaml"

    def stale_probe(args, **kwargs):
        register_context(changed)
        return CommandResult(args, context_cleanup.REMOTE_ROOT_MISSING_EXIT_CODE, "", "")

    with pytest.raises(ContextError, match="changed"):
        context_cleanup.ensure_remote_only_root_available(entry, runner=stale_probe)
    assert load_registry().contexts["remote"] == changed
