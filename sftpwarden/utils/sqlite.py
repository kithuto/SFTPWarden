"""Consistent physical SQLite snapshots, including committed WAL transactions."""

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def snapshot_sqlite(source_path: Path, target: Path) -> None:
    """Create a private, self-contained database at a new target path."""
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with (
            closing(
                sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
            ) as source,
            closing(sqlite3.connect(target)) as destination,
        ):
            deadline = time.monotonic() + 20

            def progress(status: int, remaining: int, total: int) -> None:
                """Bound busy retries as well as long-running page copies."""
                if time.monotonic() >= deadline:
                    raise TimeoutError("SQLite snapshot exceeded its 20-second deadline.")

            source.backup(destination, pages=256, progress=progress, sleep=0.1)
            destination.execute("pragma journal_mode=delete")
        with target.open("r+b") as handle:
            os.fsync(handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
