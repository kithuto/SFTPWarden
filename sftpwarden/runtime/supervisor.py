"""Own sshd and the policy worker under tini, with bounded shutdown."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from sftpwarden.runtime.configuration import prepare_runtime_configuration
from sftpwarden.runtime.execution import execute_pass, stop_process
from sftpwarden.runtime.lifecycle import (
    HEARTBEAT_SECONDS,
    STARTUP_SECONDS,
    atomic_json,
    process_record,
)


def supervise(config_path: str, command: list[str]) -> int:
    """Start policy before sshd, then stop both children if either exits."""
    config = prepare_runtime_configuration(config_path)
    directory = Path(config.server.state_dir)
    lifecycle = {
        "version": 1,
        "generation": uuid4().hex,
        "owner": process_record(os.getpid()),
        "sync_enabled": config.sync.enabled,
        "interval": config.sync.interval_seconds,
        "phase": "starting",
        "heartbeat": time.monotonic(),
    }
    children: list[subprocess.Popen[str]] = []
    stopping = False

    def terminate(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True
        if lifecycle["phase"] == "starting":
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    atomic_json(directory / "lifecycle.json", lifecycle)
    try:
        execute_pass(config_path, force=True, timeout=STARTUP_SECONDS)
        sshd = subprocess.Popen(command, text=True, start_new_session=True)
        children.append(sshd)
        lifecycle["sshd"] = process_record(sshd.pid)
        if config.sync.enabled:
            worker = subprocess.Popen(
                [sys.executable, "-m", "sftpwarden.runtime.worker", config_path],
                text=True,
                start_new_session=True,
            )
            children.append(worker)
            lifecycle["worker"] = process_record(worker.pid)
        lifecycle["phase"] = "running"
        while not stopping:
            if any(child.poll() is not None for child in children):
                return 1
            lifecycle["heartbeat"] = time.monotonic()
            atomic_json(directory / "lifecycle.json", lifecycle)
            time.sleep(HEARTBEAT_SECONDS)
        return 0
    finally:
        lifecycle["phase"] = "stopping"
        try:
            atomic_json(directory / "lifecycle.json", lifecycle)
        finally:
            for child in reversed(children):
                stop_process(child)


def main() -> int:
    """Run the container lifecycle without logging sensitive exception details."""
    try:
        return supervise(os.environ["SFTPWARDEN_CONFIG"], sys.argv[1:])
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001
        print("Runtime startup or supervision failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
