"""Linux process identity and lock deadlines use deterministic boundary adapters."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from sftpwarden.runtime import lifecycle, probes
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("state", ["R", "Z", "missing", "malformed"])
def test_process_identity_rejects_zombies_missing_and_malformed_records(monkeypatch, state):
    def read():
        if state == "missing":
            raise FileNotFoundError
        if state == "malformed":
            return "invalid"
        return "42 (worker (subprocess)) " + " ".join([state, *(["0"] * 18), "12345"])

    monkeypatch.setattr(lifecycle, "Path", lambda path: SimpleNamespace(read_text=read))
    assert lifecycle.process_record(42) == {"pid": 42, "start": "12345" if state == "R" else None}
    assert lifecycle.process_matches({"pid": 42, "start": "12345"}) == (state == "R")


@pytest.mark.parametrize("timeout", [False, True])
def test_runtime_lock_retries_with_a_deadline_and_closes_its_descriptor(
    tmp_path, monkeypatch, timeout
):
    calls, closed, sleeps = [], [], []

    def flock(fd, flags):
        calls.append(fd)
        if timeout or len(calls) == 1:
            raise BlockingIOError

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(flock=flock, LOCK_EX=1, LOCK_NB=2))
    monkeypatch.setattr(
        lifecycle,
        "os",
        SimpleNamespace(
            open=lambda *a: 1234, close=closed.append, O_CREAT=1, O_RDWR=2, O_NOFOLLOW=4
        ),
    )
    monkeypatch.setattr(lifecycle.time, "sleep", sleeps.append)
    if timeout:
        with (
            pytest.raises(RuntimeError, match="timed out"),
            lifecycle.runtime_lock(tmp_path, timeout=0),
        ):
            pytest.fail("entered a held lock")
    else:
        with lifecycle.runtime_lock(tmp_path):
            assert len(calls) == 2
        assert len(sleeps) == 1
    assert closed == [1234]


@pytest.mark.parametrize("available", [False, True])
def test_listener_probe_is_local_and_bounded(monkeypatch, available):
    def connect(endpoint, timeout):
        assert endpoint == ("127.0.0.1", 22) and timeout == 1
        if not available:
            raise OSError("not listening")
        return nullcontext()

    monkeypatch.setattr(probes.socket, "create_connection", connect)
    assert probes.sshd_accepting() == available
