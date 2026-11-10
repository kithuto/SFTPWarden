"""Lifecycle ownership and entry-point failure handling without host account changes."""

import copy
import signal
from types import SimpleNamespace

import pytest

from sftpwarden.config import default_project_config
from sftpwarden.runtime import supervisor, worker


@pytest.fixture
def supervised(tmp_path, monkeypatch):
    config = default_project_config("supervised")
    config.server.state_dir = str(tmp_path)
    records, children, stopped, passes, handlers = [], [], [], [], {}
    monkeypatch.setattr(supervisor, "prepare_runtime_configuration", lambda _: config)
    monkeypatch.setattr(
        supervisor, "atomic_json", lambda path, state: records.append(copy.deepcopy(state))
    )
    monkeypatch.setattr(supervisor, "process_record", lambda pid: {"pid": pid, "start": str(pid)})
    monkeypatch.setattr(
        supervisor.signal, "signal", lambda number, callback: handlers.setdefault(number, callback)
    )
    monkeypatch.setattr(supervisor, "execute_pass", lambda *a, **k: passes.append((a, k)))
    monkeypatch.setattr(supervisor, "stop_process", lambda process: stopped.append(process.pid))

    def spawn(command, **kwargs):
        process = SimpleNamespace(
            pid=100 + len(children), command=command, options=kwargs, result=None
        )
        process.poll = lambda: process.result
        children.append(process)
        return process

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        supervisor.time, "sleep", lambda _: handlers[signal.SIGTERM](signal.SIGTERM, None)
    )
    return SimpleNamespace(
        config=config,
        records=records,
        children=children,
        stopped=stopped,
        passes=passes,
        handlers=handlers,
    )


@pytest.mark.parametrize("sync", [False, True])
def test_supervisor_applies_initial_policy_then_owns_and_stops_every_child(supervised, sync):
    supervised.config.sync.enabled = sync
    assert supervisor.supervise("config.yaml", ["sshd", "-D"]) == 0
    assert supervised.passes == [(("config.yaml",), {"force": True, "timeout": 60})]
    assert len(supervised.children) == (2 if sync else 1)
    assert all(child.options["start_new_session"] for child in supervised.children)
    assert supervised.stopped == [child.pid for child in reversed(supervised.children)]
    assert [record["phase"] for record in supervised.records] == ["starting", "running", "stopping"]
    assert supervised.records[1]["sync_enabled"] == sync
    assert ("worker" in supervised.records[1]) == sync


def test_child_exit_never_leaves_the_other_child_serving(supervised, monkeypatch):
    spawn = supervisor.subprocess.Popen

    def exited(command, **kwargs):
        process = spawn(command, **kwargs)
        if len(supervised.children) == 2:
            process.result = 0
        return process

    monkeypatch.setattr(supervisor.subprocess, "Popen", exited)
    assert supervisor.supervise("config.yaml", ["sshd"]) == 1
    assert supervised.stopped == [101, 100]
    assert supervised.records[-1]["phase"] == "stopping"


def test_worker_spawn_failure_still_reaps_sshd(supervised, monkeypatch):
    spawn = supervisor.subprocess.Popen

    def fail_second(command, **kwargs):
        if supervised.children:
            raise OSError("worker cannot start")
        return spawn(command, **kwargs)

    monkeypatch.setattr(supervisor.subprocess, "Popen", fail_second)
    with pytest.raises(OSError, match="worker cannot start"):
        supervisor.supervise("config.yaml", ["sshd"])
    assert supervised.stopped == [100]


@pytest.mark.parametrize("interrupt", [False, True])
def test_initial_policy_failure_or_signal_never_starts_sshd(supervised, monkeypatch, interrupt):
    def interrupted(*args, **kwargs):
        if interrupt:
            supervised.handlers[signal.SIGTERM](signal.SIGTERM, None)
        raise OSError("provider unavailable")

    monkeypatch.setattr(supervisor, "execute_pass", interrupted)
    with pytest.raises(KeyboardInterrupt if interrupt else OSError):
        supervisor.supervise("config.yaml", ["sshd"])
    assert supervised.children == []
    assert supervised.records[-1]["phase"] == "stopping"


def test_status_publication_failure_cannot_skip_child_cleanup(supervised, monkeypatch):
    def record(path, state):
        if state["phase"] == "stopping":
            raise OSError("state storage unavailable")

    monkeypatch.setattr(supervisor, "atomic_json", record)
    with pytest.raises(OSError, match="storage unavailable"):
        supervisor.supervise("config.yaml", ["sshd"])
    assert supervised.stopped == [101, 100]


@pytest.mark.parametrize(
    "error,expected", [(None, 7), (KeyboardInterrupt(), 0), (OSError("secret-sentinel"), 1)]
)
def test_supervisor_entry_point_reports_private_errors(monkeypatch, capsys, error, expected):
    monkeypatch.setenv("SFTPWARDEN_CONFIG", "selected.yaml")
    monkeypatch.setattr(supervisor.sys, "argv", ["supervisor", "sshd", "-D"])

    def run(path, command):
        assert path == "selected.yaml" and command == ["sshd", "-D"]
        if error:
            raise error
        return 7

    monkeypatch.setattr(supervisor, "supervise", run)
    assert supervisor.main() == expected
    output = capsys.readouterr()
    assert output.out == "" and "secret-sentinel" not in output.err
    assert bool(output.err) == (expected == 1)


@pytest.mark.parametrize("once,force", [(False, False), (True, False), (True, True)])
def test_worker_entry_point_selects_the_requested_operation(monkeypatch, capsys, once, force):
    calls, handlers = [], []
    arguments = [
        "worker",
        *(["--once"] if once else []),
        "selected.yaml",
        *(["--force"] if force else []),
    ]
    monkeypatch.setattr(worker.sys, "argv", arguments)
    monkeypatch.setattr(worker.signal, "signal", lambda *args: handlers.append(args))
    monkeypatch.setattr(
        worker, "apply_once", lambda path, **kwargs: calls.append((path, kwargs)) or "applied"
    )
    monkeypatch.setattr(worker, "run_sync_loop", lambda path: calls.append((path, {})))
    assert worker.main() == 0
    assert calls == [("selected.yaml", {"force": force} if once else {})]
    assert capsys.readouterr().out == ("applied\n" if once else "")
    with pytest.raises(KeyboardInterrupt):
        handlers[0][1](signal.SIGTERM, None)


@pytest.mark.parametrize(
    "error,expected", [(KeyboardInterrupt(), 0), (OSError("secret-sentinel"), 1)]
)
def test_worker_failure_output_is_private(monkeypatch, capsys, error, expected):
    monkeypatch.setattr(worker.sys, "argv", ["worker", "selected.yaml"])
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)

    def fail(path):
        raise error

    monkeypatch.setattr(worker, "run_sync_loop", fail)
    assert worker.main() == expected
    output = capsys.readouterr()
    assert output.out == "" and "secret-sentinel" not in output.err
    assert bool(output.err) == bool(expected)
