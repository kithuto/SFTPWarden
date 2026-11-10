"""Descriptor ownership and close ordering without creating privileged host directories."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.runtime import filesystem


@pytest.mark.parametrize("path", ["relative", "/", "/managed/../other"])
def test_private_directory_requires_dedicated_absolute_path(path):
    with pytest.raises(ValueError, match="dedicated"):
        filesystem.open_private_directory(Path(path))


@pytest.mark.parametrize("problem", [None, "owner", "writable", "symlink"])
def test_private_directory_walk_checks_each_parent_and_closes_on_error(monkeypatch, problem):
    opened, closed, created = [], [], []

    def open_path(path, flags, **kwargs):
        if path == "keys" and problem == "symlink":
            raise OSError("symlink rejected")
        descriptor = 100 + len(opened)
        opened.append((path, kwargs.get("dir_fd"), descriptor))
        return descriptor

    fake_os = SimpleNamespace(
        O_RDONLY=0,
        O_DIRECTORY=1,
        O_NOFOLLOW=2,
        open=open_path,
        close=closed.append,
        fstat=lambda fd: SimpleNamespace(
            st_uid=99 if problem == "owner" else 0,
            st_mode=0o777 if problem == "writable" else 0o755,
        ),
        mkdir=lambda path, **kwargs: created.append((path, kwargs)),
    )
    monkeypatch.setattr(filesystem, "os", fake_os)
    # PurePosixPath also models container paths on a native Windows test host.
    from pathlib import PurePosixPath

    monkeypatch.setattr(filesystem, "Path", PurePosixPath)
    if problem:
        with pytest.raises((ValueError, OSError)):
            filesystem.open_private_directory(PurePosixPath("/managed/keys"))
        assert set(closed) == {item[2] for item in opened}
    else:
        assert filesystem.open_private_directory(PurePosixPath("/managed/keys")) == 102
        assert opened == [("/", None, 100), ("managed", 100, 101), ("keys", 101, 102)]
        assert closed == [100, 101]
        assert all(kwargs["mode"] == 0o700 for _, kwargs in created)
