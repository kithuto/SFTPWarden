"""Portable publication and process locks for operator-owned local files."""

from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


def sync_directory(directory: Path) -> None:
    """Persist renames where directory fsync is supported by the platform."""
    if sys.platform == "win32":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def reject_symlink(path: Path) -> None:
    """Reject link destinations rather than overwriting an unexpected object."""
    if path.is_symlink():
        raise OSError(errno.ELOOP, "Refusing a symbolic link destination", str(path))


def atomic_bytes(path: Path, content: bytes, *, owner: tuple[int, int] | None = None) -> None:
    """Publish a complete private file using a unique same-directory temporary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if owner is not None:
                os.fchown(handle.fileno(), *owner)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        reject_symlink(path)
        os.replace(staged, path)
        sync_directory(path.parent)
    finally:
        staged.unlink(missing_ok=True)


@contextmanager
def atomic_binary_output(path: Path, *, owner: tuple[int, int] | None = None) -> Iterator[BinaryIO]:
    """Stream a private artifact and publish it only after successful completion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(path)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if owner is not None:
                os.fchown(handle.fileno(), *owner)
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        reject_symlink(path)
        os.replace(staged, path)
        sync_directory(path.parent)
    finally:
        staged.unlink(missing_ok=True)


def lock_path(path: Path) -> Path:
    """Return the stable lock name shared by all writers of a local file."""
    return path.with_name(f".{path.name}.lock")


def migration_marker(path: Path) -> Path:
    """Return the durable notice preventing writes through a migrated old path."""
    return path.with_name(f".{path.name}.migrated")


def pending_deploy_marker(path: Path) -> Path:
    """Return the notice blocking edits until a mount transition is deployed."""
    return path.with_name(f".{path.name}.pending-deploy")


@contextmanager
def file_lock(
    path: Path, *, timeout: float = 5.0, owner: tuple[int, int] | None = None
) -> Iterator[None]:
    """Lock a separate stable file on POSIX and Windows; never unlink the lock."""
    target = lock_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(target)
    descriptor = os.open(target, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "Lock must be a regular file", str(target))
        if owner is not None:
            os.fchown(descriptor, *owner)
        if sys.platform == "win32":
            import msvcrt

            # Windows byte-range locks need one persistent byte, even on empty files.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        else:
            import fcntl

        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for file lock: {target}") from exc
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        try:
            if acquired:
                if sys.platform == "win32":
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
