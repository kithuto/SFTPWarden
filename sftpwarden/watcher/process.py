"""Private liveness evidence shared by native and container watcher installations."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from sftpwarden.utils._version import get_version
from sftpwarden.utils.paths import app_home
from sftpwarden.utils.persistence import atomic_bytes
from sftpwarden.watcher.polling import checkpoint_owner


def process_path() -> Path:
    """Return the liveness file shared with a container watcher."""
    return app_home() / "watcher" / "checkpoints" / "process.json"


def publish_process(instance: str, started: float) -> None:
    """Publish an instance-specific heartbeat after completing a polling pass."""
    path = process_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_bytes(
        path,
        json.dumps(
            {
                "instance": instance,
                "pid": os.getpid(),
                "version": get_version(),
                "python": sys.executable,
                "started": started,
                "heartbeat": time.time(),
            }
        ).encode(),
        owner=checkpoint_owner(path),
    )


def identity() -> tuple[str, float]:
    """Create a unique watcher process identity."""
    return uuid4().hex, time.time()
