"""Cleanup tool behavior tests."""

from __future__ import annotations

import os
from pathlib import Path

import tools.clean_artifacts as cleanup_tool


def test_cleanup_removes_selected_artifact_groups(tmp_path: Path, monkeypatch) -> None:
    """Remove Python, docs, package, and temp artifacts from safe locations."""
    root = tmp_path / "repo"
    temp = tmp_path / "tmp"
    (root / "docs" / "_build").mkdir(parents=True)
    (root / "dist").mkdir()
    (root / ".pytest_cache").mkdir()
    (root / "sftpwarden" / "__pycache__").mkdir(parents=True)
    (root / "sftpwarden" / "__pycache__" / "module.pyc").write_text("", encoding="utf-8")
    (root / ".coverage").write_text("coverage", encoding="utf-8")
    (temp / "sftpwarden-smoke").mkdir(parents=True)
    monkeypatch.setattr(cleanup_tool, "ROOT", root)
    monkeypatch.setattr(cleanup_tool, "TMP_ROOT", temp)

    result = cleanup_tool.cleanup(
        {"python", "docs", "package", "temp"}, docker=False, dry_run=False
    )

    assert result.skipped == []
    assert not (root / "docs" / "_build").exists()
    assert not (root / "dist").exists()
    assert not (root / ".pytest_cache").exists()
    assert not (root / "sftpwarden" / "__pycache__").exists()
    assert not (root / ".coverage").exists()
    assert not (temp / "sftpwarden-smoke").exists()


def test_cleanup_dry_run_and_tox_safety(tmp_path: Path, monkeypatch) -> None:
    """Report dry-run removals and avoid deleting active tox environments."""
    root = tmp_path / "repo"
    temp = tmp_path / "tmp"
    (root / ".tox").mkdir(parents=True)
    monkeypatch.setattr(cleanup_tool, "ROOT", root)
    monkeypatch.setattr(cleanup_tool, "TMP_ROOT", temp)
    monkeypatch.delenv("TOX_ENV_NAME", raising=False)

    dry_run = cleanup_tool.cleanup({"tox"}, docker=False, dry_run=True)

    assert dry_run.removed == [str(root / ".tox")]
    assert (root / ".tox").exists()

    monkeypatch.setenv("TOX_ENV_NAME", "py314")
    active_tox = cleanup_tool.cleanup({"tox"}, docker=False, dry_run=False)

    assert active_tox.skipped == [".tox cleanup skipped from inside tox"]
    assert (root / ".tox").exists()


def test_cleanup_retries_read_only_directory_removal(tmp_path: Path, monkeypatch) -> None:
    """Clear read-only artifact bits when shutil reports a permission error."""
    root = tmp_path / "repo"
    temp = tmp_path / "tmp"
    cache = root / "sftpwarden" / "__pycache__"
    cache.mkdir(parents=True)
    monkeypatch.setattr(cleanup_tool, "ROOT", root)
    monkeypatch.setattr(cleanup_tool, "TMP_ROOT", temp)
    monkeypatch.setattr(cleanup_tool, "REMOVE_RETRY_DELAY_SECONDS", 0)

    def rmtree_with_permission_callback(path: Path, *, onexc=None, onerror=None) -> None:
        error = PermissionError("access denied")
        path_name = os.fspath(path)
        if onexc is not None:
            onexc(os.rmdir, path_name, error)
            return
        assert onerror is not None
        onerror(os.rmdir, path_name, (PermissionError, error, None))

    monkeypatch.setattr(cleanup_tool.shutil, "rmtree", rmtree_with_permission_callback)

    result = cleanup_tool.cleanup({"python"}, docker=False, dry_run=False)

    assert result.skipped == []
    assert result.removed == [str(cache)]
    assert not cache.exists()


def test_cleanup_skips_artifact_that_stays_locked(tmp_path: Path, monkeypatch) -> None:
    """Continue cleanup when an artifact remains locked after retries."""
    root = tmp_path / "repo"
    temp = tmp_path / "tmp"
    cache = root / "sftpwarden" / "__pycache__"
    cache.mkdir(parents=True)
    monkeypatch.setattr(cleanup_tool, "ROOT", root)
    monkeypatch.setattr(cleanup_tool, "TMP_ROOT", temp)
    monkeypatch.setattr(cleanup_tool, "REMOVE_RETRY_DELAY_SECONDS", 0)

    def locked_rmtree(path: Path, **kwargs: object) -> None:
        raise PermissionError("still locked")

    monkeypatch.setattr(cleanup_tool.shutil, "rmtree", locked_rmtree)

    result = cleanup_tool.cleanup({"python"}, docker=False, dry_run=False)

    assert result.removed == []
    assert len(result.skipped) == 1
    assert "could not remove" in result.skipped[0]
    assert cache.exists()
