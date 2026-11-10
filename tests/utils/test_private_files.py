from __future__ import annotations

import os
from pathlib import Path

import pytest

from sftpwarden.config import default_project_config, write_config
from sftpwarden.utils.files import write_private_text


@pytest.mark.parametrize("project_config", [False, True])
def test_private_text_publication_failure_leaves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_config: bool
) -> None:
    target = tmp_path / "private.yaml"
    target.write_text("previous complete contents")

    def fail_replace(source, destination):
        if os.name != "nt":
            assert Path(source).stat().st_mode & 0o777 == 0o600
        raise OSError("injected failed publication")

    monkeypatch.setattr("sftpwarden.utils.persistence.os.replace", fail_replace)
    with pytest.raises(OSError, match="failed publication"):
        if project_config:
            write_config(target, default_project_config("private"))
        else:
            write_private_text(target, "new confidential payload")
    assert target.read_text() == "previous complete contents"
    expected = {target}
    if project_config:
        # The stable lock inode must survive failed publication for other writers.
        expected.add(tmp_path / ".private.yaml.lock")
    assert set(tmp_path.iterdir()) == expected


def test_private_text_refuses_link_destination(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    target = tmp_path / "private.yaml"
    try:
        target.symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or elevated privilege")
        raise
    with pytest.raises(OSError):
        write_private_text(target, "changed")
    assert outside.read_text() == "untouched"


def test_private_text_replaces_complete_utf8_document(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "private.yaml"
    write_private_text(target, "first\n")
    write_private_text(target, "second: 日本\n")
    assert target.read_text(encoding="utf-8") == "second: 日本\n"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
