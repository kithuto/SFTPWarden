"""Portable adapters for Linux-only publication boundaries, not host privilege changes."""

import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def reconciliation_lock(monkeypatch):
    """Isolate policy tests from the separately tested Linux flock boundary."""
    from sftpwarden.runtime import core

    monkeypatch.setattr(core, "runtime_lock", lambda directory: nullcontext())


@pytest.fixture
def linux_publication(tmp_path, monkeypatch):
    directory = tmp_path / "mounted"
    directory.mkdir()
    descriptor = 1234567
    changes, closed = [], []
    original_close, original_sync = os.close, os.fsync
    original_open = os.open
    original_chmod = getattr(os, "fchmod", lambda *args: None)
    monkeypatch.setattr(os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    monkeypatch.setattr(
        os, "fchown", lambda fd, uid, gid: changes.append((fd, uid, gid)), raising=False
    )
    monkeypatch.setattr(
        os,
        "fchmod",
        lambda fd, mode: None if fd == descriptor else original_chmod(fd, mode),
        raising=False,
    )
    monkeypatch.setattr(
        os, "close", lambda fd: closed.append(fd) if fd == descriptor else original_close(fd)
    )
    monkeypatch.setattr(os, "fsync", lambda fd: None if fd == descriptor else original_sync(fd))
    if os.name == "nt":
        # Linux permits fchmod/fsync on read-only descriptors; Windows requires
        # write access. Adapt only this simulated container mount, not other IO.
        def open_mounted(path, flags, *args, **kwargs):
            if Path(path).is_relative_to(directory) and not flags & (os.O_WRONLY | os.O_RDWR):
                flags |= os.O_RDWR
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", open_mounted)

    def attach(module):
        monkeypatch.setattr(module, "open_private_directory", lambda path: descriptor)
        monkeypatch.setattr(
            module,
            "Path",
            lambda value: directory if str(value) == f"/proc/self/fd/{descriptor}" else Path(value),
        )
        if hasattr(module, "sync_directory"):
            monkeypatch.setattr(module, "sync_directory", lambda path: None)

    return SimpleNamespace(
        directory=directory, descriptor=descriptor, changes=changes, closed=closed, attach=attach
    )
