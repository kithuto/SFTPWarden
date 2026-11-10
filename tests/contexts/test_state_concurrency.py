from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from sftpwarden.config.global_config import load_global_config
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    ContextType,
    load_registry,
    save_registry,
)

WORKER = """
import sys
from sftpwarden.contexts import registry
from sftpwarden.cli_commands import context, config
from sftpwarden.watcher import core as watcher
kind, role = sys.argv[1:]
module = registry if kind == 'register' else context if kind == 'fields' else (
    config if role == 'first' else watcher
)
attribute = 'load_global_config' if kind == 'global' else 'load_registry'
original = getattr(module, attribute)
first = True
def read():
    global first
    value = original()
    if first:
        first = False
        print('snapshot', flush=True)
        if role == 'first':
            assert sys.stdin.readline().strip() == 'go'
    return value
setattr(module, attribute, read)
if kind == 'register':
    registry.register_context(registry.ContextEntry(name=role, type='local'))
elif kind == 'fields':
    context.update_context_field(
        'shared', 'critical' if role == 'first' else 'provider',
        'true' if role == 'first' else 'csv', remote_url=None, root=None,
        remote_user=None, port=None, remote_root=None, remote_only=False,
        delete_old_root=False, yes=True,
    )
elif role == 'first':
    config.config_default_provider('csv')
else:
    watcher.install_watcher(mode='systemd', activate=False)
"""


def run_overlapping_edits(kind: str, home: Path) -> None:
    environment = {**os.environ, "SFTPWARDEN_HOME": str(home)}
    workers: list[subprocess.Popen[str]] = []
    ready: queue.Queue[str] = queue.Queue()

    def read_ready(process):
        ready.put(process.stdout.readline().strip())

    def start(role):
        process = subprocess.Popen(
            [sys.executable, "-c", WORKER, kind, role],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        workers.append(process)
        threading.Thread(target=read_ready, args=(process,), daemon=True).start()
        return process

    try:
        first = start("first")
        assert ready.get(timeout=15) == "snapshot"
        second = start("second")
        waiting_for_second = False
        try:
            assert ready.get(timeout=2) == "snapshot"
        except queue.Empty:
            # Correct locking keeps the second read behind the first transaction.
            waiting_for_second = True
        else:
            # Baseline: finish the second write before releasing the stale first writer.
            _, errors = second.communicate(timeout=15)
            assert second.returncode == 0, errors
        first.stdin.write("go\n")
        first.stdin.flush()
        for index, process in enumerate(workers):
            if index == 1 and waiting_for_second:
                assert ready.get(timeout=15) == "snapshot"
            _, errors = process.communicate(timeout=15)
            assert process.returncode == 0, errors
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("kind", ["register", "fields", "global"])
def test_independent_host_state_writers(tmp_path, monkeypatch, kind):
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    if kind == "fields":
        entry = ContextEntry(name="shared", type=ContextType.LOCAL)
        save_registry(ContextRegistry(default=entry.name, contexts={entry.name: entry}))
    run_overlapping_edits(kind, home)
    if kind == "register":
        assert set(load_registry().contexts) == {"first", "second"}
    elif kind == "fields":
        entry = load_registry().contexts["shared"]
        assert entry.critical and entry.provider.value == "csv"
    else:
        config = load_global_config()
        assert config.default_provider.value == "csv"
        assert config.watcher.installed and config.watcher.mode == "systemd"
