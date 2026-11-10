import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import sftpwarden.runtime.core as runtime
import sftpwarden.runtime.lifecycle as lifecycle
import sftpwarden.runtime.probes as probes
from sftpwarden.config import default_project_config
from sftpwarden.runtime.execution import execute_pass
from sftpwarden.utils.errors import RuntimeError


def test_t04_sync_loop_recovers_without_another_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    config = default_project_config("recovery")
    config.sync.interval_seconds = 5
    monkeypatch.setattr("sftpwarden.runtime.configuration.load_runtime_config", lambda _: config)
    attempts: list[int] = []

    def attempt(*args: object, **kwargs: object) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("provider unavailable")
        return "No user changes detected."

    def sleep(seconds: float) -> None:
        if len(attempts) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "execute_pass", attempt)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        runtime.run_sync_loop(Path("config.yaml"))
    assert len(attempts) == 2


@pytest.mark.parametrize("elapsed,expected", [(0, 5), (2, 3), (8, 0)])
def test_sync_interval_does_not_add_a_full_delay_after_slow_work(monkeypatch, elapsed, expected):
    config = default_project_config("cadence")
    config.sync.interval_seconds = 5
    monkeypatch.setattr("sftpwarden.runtime.configuration.load_runtime_config", lambda _: config)
    clock = iter([100, 100 + elapsed])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    completed = []
    monkeypatch.setattr(
        runtime, "execute_pass", lambda _: completed.append(True) or "No user changes detected."
    )

    def sleep(seconds):
        assert completed == [True]
        assert seconds == expected
        raise KeyboardInterrupt

    monkeypatch.setattr(
        runtime, "wait_for_provider_change", lambda _config, _observed, seconds: sleep(seconds)
    )
    with pytest.raises(KeyboardInterrupt):
        runtime.run_sync_loop("config.yaml")


@pytest.fixture
def ready_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lifecycle.atomic_json(
        tmp_path / "lifecycle.json",
        {
            "version": 1,
            "generation": "current",
            "phase": "running",
            "heartbeat": 100,
            "sync_enabled": True,
            "interval": 5,
            "owner": {},
            "sshd": {},
            "worker": {},
        },
    )
    lifecycle.atomic_json(
        tmp_path / "sync-status.json",
        {
            "version": 1,
            "generation": "current",
            "last_success": "UTC time",
            "success_monotonic": 100,
            "pending": False,
            "failure": None,
        },
    )
    monkeypatch.setattr(probes, "process_matches", lambda _: True)
    monkeypatch.setattr(probes, "sshd_accepting", lambda: True)
    return tmp_path


@pytest.mark.parametrize("age,expected", [(4.999, True), (5, True), (5.001, False), (-1, False)])
def test_t03_supervisor_heartbeat_boundaries(
    ready_status: Path, age: float, expected: bool
) -> None:
    assert probes.probe_runtime(ready_status, now=100 + age)[0] is expected


@pytest.mark.parametrize("age,expected", [(69.999, True), (70, True), (70.001, False), (-1, False)])
def test_t04_progress_freshness_boundaries(ready_status: Path, age: float, expected: bool) -> None:
    state = lifecycle.read_json(ready_status / "lifecycle.json")
    state["heartbeat"] = 100 + age
    lifecycle.atomic_json(ready_status / "lifecycle.json", state)
    assert lifecycle.freshness_seconds(5) == 70
    assert probes.probe_runtime(ready_status, now=100 + age)[0] is expected


@pytest.mark.parametrize("age,expected", [(29.999, True), (30, True), (30.001, False)])
def test_t03_pending_attempt_deadline(ready_status: Path, age: float, expected: bool) -> None:
    status = lifecycle.read_json(ready_status / "sync-status.json")
    status.update({"pending": True, "attempt_monotonic": 100 - age})
    lifecycle.atomic_json(ready_status / "sync-status.json", status)
    assert probes.probe_runtime(ready_status, now=100)[0] is expected


def test_t04_provider_failure_is_not_a_liveness_failure(ready_status: Path) -> None:
    status = lifecycle.read_json(ready_status / "sync-status.json")
    status["failure"] = "ProviderError"
    lifecycle.atomic_json(ready_status / "sync-status.json", status)
    assert not probes.probe_runtime(ready_status, now=100)[0]
    assert probes.probe_runtime(ready_status, probe="liveness", now=100)[0]


@pytest.mark.parametrize("change", ["generation", "no-success", "missing", "bad-json"])
def test_t03_unusable_status_cannot_be_healthy(ready_status: Path, change: str) -> None:
    path = ready_status / "sync-status.json"
    status = lifecycle.read_json(path)
    if change == "generation":
        status["generation"] = "previous"
    elif change == "no-success":
        status.pop("last_success")
    if change == "missing":
        path.unlink()
    elif change == "bad-json":
        path.write_text("[")
    else:
        lifecycle.atomic_json(path, status)
    assert not probes.probe_runtime(ready_status, now=100)[0]


def test_t03_process_death_and_closed_listener(
    ready_status: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probes, "process_matches", lambda _: False)
    assert not probes.probe_runtime(ready_status, probe="liveness", now=100)[0]
    monkeypatch.setattr(probes, "process_matches", lambda _: True)
    monkeypatch.setattr(probes, "sshd_accepting", lambda: False)
    assert not probes.probe_runtime(ready_status, now=100)[0]


def test_t03_sync_disabled_still_requires_initial_policy(ready_status: Path) -> None:
    state = lifecycle.read_json(ready_status / "lifecycle.json")
    state.update({"sync_enabled": False, "heartbeat": 1000})
    lifecycle.atomic_json(ready_status / "lifecycle.json", state)
    assert probes.probe_runtime(ready_status, now=1000)[0]
    (ready_status / "sync-status.json").unlink()
    assert not probes.probe_runtime(ready_status, now=1000)[0]


def test_t03_pid_reuse_and_zombies_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle, "process_identity", lambda pid: "new")
    assert not lifecycle.process_matches({"pid": 42, "start": "old"})
    assert lifecycle.process_matches({"pid": 42, "start": "new"})
    assert not lifecycle.process_matches({"pid": 42})
    assert not lifecycle.process_matches(None)


@pytest.mark.skipif(os.name == "nt", reason="Runtime process groups and flock require POSIX")
def test_t05_runtime_lock_excludes_a_second_process(tmp_path: Path) -> None:
    with lifecycle.runtime_lock(tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; from sftpwarden.runtime.lifecycle import runtime_lock; "
                f"\nwith runtime_lock(Path({str(tmp_path)!r}), timeout=0): pass",
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode != 0
        assert "lock timed out" in result.stderr
    with lifecycle.runtime_lock(tmp_path, timeout=0):
        pass


@pytest.mark.skipif(os.name == "nt", reason="Runtime process groups require POSIX")
def test_t03_reconciliation_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    real_popen = subprocess.Popen
    processes = []

    def sleeping_process(*args: object, **kwargs: object) -> subprocess.Popen:
        process = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("sftpwarden.runtime.execution.subprocess.Popen", sleeping_process)
    with pytest.raises(RuntimeError, match="deadline"):
        execute_pass("unused", timeout=0.05)
    assert processes[0].poll() is not None


def test_t05_private_state_is_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    lifecycle.atomic_json(path, {"complete": True})
    assert json.loads(path.read_text()) == {"complete": True}
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("pending,failure", [(False, "ProviderError"), (True, None)])
def test_t04_retry_cannot_mask_an_unapplied_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reconciliation_lock,
    pending: bool,
    failure: str | None,
) -> None:
    config = default_project_config("retry")
    config.server.state_dir = str(tmp_path)
    lifecycle.atomic_json(tmp_path / "lifecycle.json", {"generation": "current"})
    lifecycle.atomic_json(
        tmp_path / "sync-status.json",
        {
            "generation": "current",
            "last_success": "previous",
            "pending": pending,
            "failure": failure,
        },
    )
    monkeypatch.setattr(runtime, "load_config", lambda _: config)

    def reconcile(*args: object, **kwargs: object) -> str:
        status = lifecycle.read_json(tmp_path / "sync-status.json")
        assert status["pending"]
        assert status["failure"] == ("InterruptedAttempt" if pending else failure)
        return "recovered"

    monkeypatch.setattr(runtime, "_apply_once", reconcile)
    assert runtime.apply_once("config") == "recovered"
    status = lifecycle.read_json(tmp_path / "sync-status.json")
    assert not status["pending"] and status["failure"] is None
