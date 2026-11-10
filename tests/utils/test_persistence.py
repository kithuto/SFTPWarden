import errno
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.utils import persistence


def test_private_atomic_publication_preserves_previous_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "provider"
    persistence.atomic_bytes(path, b"previous")

    def fail_replace(source: Path, target: Path) -> None:
        assert source.read_bytes() == b"next"
        if os.name != "nt":
            assert source.stat().st_mode & 0o777 == 0o600
        raise OSError(errno.ENOSPC, "Injected full device")

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", fail_replace)
        with pytest.raises(OSError):
            persistence.atomic_bytes(path, b"next")
    assert path.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [path]
    persistence.atomic_bytes(path, b"committed")
    assert path.read_bytes() == b"committed"


def test_atomic_publication_refuses_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    target = tmp_path / "provider"
    try:
        target.symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or elevated privilege")
        raise
    with pytest.raises(OSError):
        persistence.atomic_bytes(target, b"changed")
    assert outside.read_bytes() == b"untouched"


def test_lock_excludes_an_independent_process_and_remains_stable(tmp_path: Path) -> None:
    target = tmp_path / "provider"
    code = """
import sys
from pathlib import Path
from sftpwarden.utils.persistence import file_lock
try:
    with file_lock(Path(sys.argv[1]), timeout=0.15):
        print('acquired', flush=True)
except TimeoutError:
    print('contended', flush=True)
"""
    with persistence.file_lock(target):
        identity = persistence.lock_path(target).stat().st_ino
        result = subprocess.run(
            [sys.executable, "-c", code, str(target)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == "contended"
        persistence.atomic_bytes(target, b"replacement")
    result = subprocess.run(
        [sys.executable, "-c", code, str(target)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "acquired"
    assert persistence.lock_path(target).stat().st_ino == identity


@pytest.mark.parametrize("failure", [None, errno.EAGAIN, errno.EIO])
def test_windows_byte_lock_retries_and_unlocks_only_after_acquisition(
    tmp_path, monkeypatch, failure
):
    calls, sleeps = [], []
    monkeypatch.setattr(persistence, "sys", SimpleNamespace(platform="win32"))

    def locking(descriptor, operation, length):
        calls.append((operation, length))
        if operation == 1 and failure and (failure == errno.EIO or len(calls) == 1):
            raise OSError(failure, "lock unavailable")

    monkeypatch.setitem(
        sys.modules, "msvcrt", SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
    )
    monkeypatch.setattr(persistence.time, "sleep", sleeps.append)
    target = tmp_path / "provider"
    if failure == errno.EIO:
        with pytest.raises(OSError), persistence.file_lock(target):
            pytest.fail("entered without a lock")
        assert calls == [(1, 1)]
    else:
        with persistence.file_lock(target):
            assert persistence.lock_path(target).read_bytes() == b"\0"
        assert calls[-1] == (2, 1)
        assert bool(sleeps) == (failure == errno.EAGAIN)


def test_contended_lock_timeout_never_unlocks_another_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "sys", SimpleNamespace(platform="win32"))
    calls = []

    def locked(fd, operation, size):
        calls.append(operation)
        raise OSError(errno.EACCES, "held elsewhere")

    monkeypatch.setitem(
        sys.modules, "msvcrt", SimpleNamespace(locking=locked, LK_NBLCK=1, LK_UNLCK=2)
    )
    with pytest.raises(TimeoutError), persistence.file_lock(tmp_path / "file", timeout=0):
        pytest.fail("entered a held lock")
    assert calls == [1]


@pytest.mark.parametrize("error", [errno.EINVAL, errno.ENOTSUP, errno.EIO])
def test_directory_sync_ignores_only_unsupported_filesystems(monkeypatch, tmp_path, error):
    monkeypatch.setattr(persistence, "sys", SimpleNamespace(platform="linux"))
    closed = []
    monkeypatch.setattr(persistence.os, "open", lambda *a, **k: 12345)
    monkeypatch.setattr(persistence.os, "close", closed.append)

    def sync(fd):
        raise OSError(error, "sync failed")

    monkeypatch.setattr(persistence.os, "fsync", sync)
    if error == errno.EIO:
        with pytest.raises(OSError):
            persistence.sync_directory(tmp_path)
    else:
        persistence.sync_directory(tmp_path)
    assert closed == [12345]


@pytest.mark.parametrize("denied", [False, True])
def test_shared_checkpoint_owner_is_set_before_publication(tmp_path, monkeypatch, denied):
    path = tmp_path / "checkpoint"
    persistence.atomic_bytes(path, b"previous")
    owners = []

    def change_owner(fd, uid, gid):
        assert os.fstat(fd).st_size == 0
        owners.append((uid, gid))
        if denied:
            raise PermissionError("ownership unavailable")

    monkeypatch.setattr(persistence.os, "fchown", change_owner, raising=False)
    if denied:
        with pytest.raises(PermissionError):
            persistence.atomic_bytes(path, b"next", owner=(1001, 1002))
        assert path.read_bytes() == b"previous"
        assert list(tmp_path.iterdir()) == [path]
    else:
        persistence.atomic_bytes(path, b"next", owner=(1001, 1002))
        assert path.read_bytes() == b"next"
    assert owners == [(1001, 1002)]


def test_shared_lock_owner_does_not_replace_the_locked_inode(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint"
    changes = []
    monkeypatch.setattr(
        persistence.os, "fchown", lambda fd, *owner: changes.append(owner), raising=False
    )
    with persistence.file_lock(path, owner=(1001, 1002)):
        identity = persistence.lock_path(path).stat().st_ino
    with persistence.file_lock(path, owner=(1001, 1002)):
        assert persistence.lock_path(path).stat().st_ino == identity
    assert changes == [(1001, 1002), (1001, 1002)]
