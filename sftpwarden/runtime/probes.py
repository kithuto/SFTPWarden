"""Local, bounded probes that never call providers or acquire the runtime lock."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Literal

from sftpwarden.runtime.lifecycle import (
    ATTEMPT_SECONDS,
    HEARTBEAT_MAX_AGE,
    freshness_seconds,
    process_matches,
    read_json,
)

Probe = Literal["readiness", "liveness", "startup"]


def sshd_accepting() -> bool:
    """Check the local listener within one second, without authenticating."""
    try:
        with socket.create_connection(("127.0.0.1", 22), timeout=1):
            return True
    except OSError:
        return False


def recent(timestamp: object, *, now: float, budget: float) -> bool:
    """Accept only finite elapsed times inside the inclusive freshness window."""
    return isinstance(timestamp, (int, float)) and 0 <= now - timestamp <= budget


def probe_runtime(
    directory: Path, *, probe: Probe = "readiness", now: float | None = None
) -> tuple[bool, str]:
    """Evaluate required processes and progress for this live runtime generation."""
    reference = time.monotonic() if now is None else now
    lifecycle = read_json(directory / "lifecycle.json")
    status = read_json(directory / "sync-status.json")
    if lifecycle.get("version") != 1 or lifecycle.get("phase") != "running":
        return False, "Runtime lifecycle has not completed startup."
    if not recent(lifecycle.get("heartbeat"), now=reference, budget=HEARTBEAT_MAX_AGE):
        return False, "Runtime supervisor heartbeat is stale."
    for process in ("owner", "sshd", *(["worker"] if lifecycle.get("sync_enabled") else [])):
        if not process_matches(lifecycle.get(process)):
            return False, f"Required runtime process is unavailable: {process}."
    if not sshd_accepting():
        return False, "OpenSSH is not accepting connections."
    if probe == "liveness":
        return True, "Required runtime processes are responsive."
    if (
        status.get("version") != 1
        or not lifecycle.get("generation")
        or status.get("generation") != lifecycle["generation"]
    ):
        return False, "Runtime policy status belongs to no current generation."
    if status.get("failure"):
        return False, "Runtime policy has an unapplied failure."
    if status.get("pending") and not recent(
        status.get("attempt_monotonic"), now=reference, budget=ATTEMPT_SECONDS
    ):
        return False, "Runtime reconciliation has exceeded its deadline."
    if status.get("last_success") is None:
        return False, "Initial policy has not been applied."
    if lifecycle.get("sync_enabled"):
        interval = lifecycle.get("interval")
        if not isinstance(interval, int) or interval < 5:
            return False, "Invalid runtime synchronization interval."
        if not recent(
            status.get("success_monotonic"), now=reference, budget=freshness_seconds(interval)
        ):
            return False, "Runtime synchronization progress is stale."
        return True, "OpenSSH and synchronization are ready."
    return True, "OpenSSH is ready; periodic synchronization is disabled."
