"""Inventory capture rejects path collisions and source changes before publication."""

import io
import stat
import tarfile
from types import SimpleNamespace

import pytest

from sftpwarden.services import backup_inventory
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize(
    "problem",
    ["duplicate", "mode", "hardlink", "changed-directory", "changed-inode", "changed-size"],
)
def test_inventory_rejects_unstable_or_unsafe_sources(tmp_path, monkeypatch, problem):
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    inventory = {}
    if problem == "duplicate":
        inventory["DATA"] = {}
    if problem == "changed-directory":
        source.unlink()
        source.mkdir()
    original_stat = source.stat()
    if problem in {"mode", "hardlink"}:
        from pathlib import Path

        original_lstat = Path.lstat

        def lstat(path):
            if path == source:
                return SimpleNamespace(
                    st_mode=stat.S_IFREG | (0o4600 if problem == "mode" else 0o600), st_nlink=2
                )
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", lstat)
    if problem in {"changed-inode", "changed-size"}:
        values = {
            name: getattr(original_stat, name)
            for name in dir(original_stat)
            if name.startswith("st_")
        }
        values["st_ino" if problem == "changed-inode" else "st_size"] += 1
        reads = []

        def fstat(fd):
            reads.append(fd)
            return (
                SimpleNamespace(**values)
                if problem == "changed-inode" or len(reads) >= 3
                else original_stat
            )

        monkeypatch.setattr(backup_inventory.os, "fstat", fstat)
    with tarfile.open(fileobj=io.BytesIO(), mode="w") as archive:
        if problem == "changed-directory":
            monkeypatch.setattr(archive, "gettarinfo", lambda *a, **k: tarfile.TarInfo("data"))
        with pytest.raises(RuntimeError):
            backup_inventory.add_source(archive, source, "data", inventory)
    assert not inventory or problem == "duplicate"


def test_inventory_requires_member_set_and_metadata_to_match(tmp_path):
    member = tarfile.TarInfo("data")
    member.type = tarfile.DIRTYPE
    with pytest.raises(RuntimeError, match="inventory"):
        backup_inventory.validate_inventory(tmp_path, {}, {"data": member})
    with pytest.raises(RuntimeError, match="integrity"):
        backup_inventory.validate_inventory(tmp_path, {"data": {}}, {"data": member})
