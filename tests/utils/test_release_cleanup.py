"""Privileged release cleanup stays inside its registered disposable tree."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.release_validation import conftest as release


def test_cleanup_removes_owned_tree_and_accepts_missing_target(tmp_path):
    stack = release.CleanupStack(tmp_path)
    child = tmp_path / "owned"
    child.mkdir()
    (child / "file").write_text("owned")
    stack.remove_tree(child)
    stack.remove_tree(child)
    assert not child.exists()


@pytest.mark.parametrize("target", ["../outside", "child/../../outside"])
def test_cleanup_rejects_escape_before_removing_anything(tmp_path, monkeypatch, target):
    stack = release.CleanupStack(tmp_path)
    monkeypatch.setattr(release.shutil, "rmtree", lambda *a: pytest.fail("unsafe removal"))
    with pytest.raises(ValueError, match="outside"):
        stack.remove_tree(tmp_path / target)


def test_cleanup_rejects_intermediate_symlink(tmp_path, monkeypatch):
    stack = release.CleanupStack(tmp_path)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == tmp_path / "link")
    with pytest.raises(ValueError, match="symbolic link"):
        stack.remove_tree(tmp_path / "link" / "child")


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_private_container_tree_uses_scoped_linux_cleanup(tmp_path, monkeypatch, platform):
    stack = release.CleanupStack(tmp_path)
    monkeypatch.setattr(release, "sys", SimpleNamespace(platform=platform))

    def denied(path):
        raise PermissionError("container-owned data")

    monkeypatch.setattr(release.shutil, "rmtree", denied)
    calls = []
    monkeypatch.setattr(release, "run_external", lambda args, **kw: calls.append(args))
    if platform == "linux":
        stack.remove_tree(tmp_path)
        assert calls == [["sudo", "-n", "rm", "-rf", "--", str(tmp_path)]]
    else:
        with pytest.raises(PermissionError):
            stack.remove_tree(tmp_path)
        assert not calls
