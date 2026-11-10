"""Private Linux runtime state, deadlines and process identity checks."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes

ATTEMPT_SECONDS = 30
STARTUP_SECONDS = 60
LOCK_SECONDS = 5
RETRY_CAP_SECONDS = 30
HEARTBEAT_SECONDS = 1
HEARTBEAT_MAX_AGE = 5
SHUTDOWN_SECONDS = 5
SCHEDULING_ALLOWANCE = 5


def freshness_seconds(interval: int) -> int:
    """Allow one interval, attempt, maximum retry delay and scheduling allowance."""
    return interval + ATTEMPT_SECONDS + RETRY_CAP_SECONDS + SCHEDULING_ALLOWANCE


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    """Publish private JSON without exposing a partially written record."""
    atomic_bytes(path, json.dumps(data, sort_keys=True).encode("utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    """Read a private record; invalid or absent records cannot establish health."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def process_identity(pid: int) -> str | None:
    """Return Linux process start ticks, excluding zombies and dead processes."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError, ValueError):
        return None


def process_record(pid: int) -> dict[str, Any]:
    """Capture a PID and its start identity to reject stale records and PID reuse."""
    return {"pid": pid, "start": process_identity(pid)}


def process_matches(record: Any) -> bool:
    """Check that the recorded live process still belongs to this generation."""
    return (
        isinstance(record, dict)
        and isinstance(record.get("pid"), int)
        and bool(record.get("start"))
        and process_identity(record["pid"]) == record["start"]
    )


@contextmanager
def runtime_lock(directory: Path, *, timeout: float = LOCK_SECONDS) -> Iterator[None]:
    """Serialize complete Linux runtime passes using a stable no-follow lock file."""
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Runtime synchronization lock timed out.") from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)
