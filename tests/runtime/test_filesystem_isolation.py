import os
from pathlib import Path

import pytest

import sftpwarden.runtime.core as runtime
from sftpwarden.config import default_project_config
from sftpwarden.users import SFTPUser

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Linux runtime directory descriptors require POSIX"
)


@pytest.mark.skipif(os.name == "nt", reason="Privileged runtime filesystem operations are POSIX")
@pytest.mark.parametrize("relative", ["upload", "upload/nested"])
def test_t06_symlink_cannot_repermission_an_outside_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    config = default_project_config("isolation")
    config.server.data_dir = str(tmp_path / "data")
    root = tmp_path / "data" / "alice"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    (root / "upload").symlink_to(outside, target_is_directory=True)
    before = outside.stat()
    monkeypatch.setattr(runtime, "chown_path", lambda *args: None)
    monkeypatch.setattr(os, "fchown", lambda *args: None)
    with pytest.raises((OSError, runtime.RuntimeError)):
        runtime.ensure_directories(
            config,
            runtime.ResolvedUser(
                SFTPUser(username="alice", upload_dir=relative),
                10000,
                10000,
            ),
        )
    after = outside.stat()
    assert (before.st_uid, before.st_gid, before.st_mode) == (
        after.st_uid,
        after.st_gid,
        after.st_mode,
    )
    assert sentinel.read_text() == "unchanged"
    assert list(outside.iterdir()) == [sentinel]


def test_t06_parent_swap_cannot_redirect_an_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = default_project_config("race")
    config.server.data_dir = str(tmp_path / "data")
    root = tmp_path / "data" / "alice"
    upload = root / "upload"
    upload.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    before = outside.stat()
    real_open = os.open
    swapped = False

    def swap_open(path: str | Path, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "nested" and not swapped:
            swapped = True
            upload.rename(root / "retained")
            upload.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_open)
    monkeypatch.setattr(os, "fchown", lambda *args: None)
    runtime.ensure_directories(
        config,
        runtime.ResolvedUser(SFTPUser(username="alice", upload_dir="upload/nested"), 10000, 10000),
    )
    assert swapped
    assert (root / "retained" / "nested").stat().st_mode & 0o777 == 0o750
    after = outside.stat()
    assert (before.st_uid, before.st_gid, before.st_mode) == (
        after.st_uid,
        after.st_gid,
        after.st_mode,
    )
    assert list(outside.iterdir()) == [sentinel]
    assert sentinel.read_text() == "unchanged"
