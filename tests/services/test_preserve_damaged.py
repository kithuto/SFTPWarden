"""Incomplete preservation never depends on reading the broken provider."""

import json
import tarfile
from pathlib import Path

import pytest

from sftpwarden.services import preserve_damaged
from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.services.preserve_damaged import preserve_readable_project
from sftpwarden.utils.archives import extract_private_archive
from sftpwarden.utils.errors import RuntimeError


def test_missing_provider_preserves_keys_and_reports_incomplete(tmp_path):
    original = tmp_path / "damaged"
    original.mkdir()
    (original / "host_keys").mkdir()
    (original / "host_keys" / "synthetic-key").write_bytes(b"synthetic preservation sentinel")
    (original / "sftpwarden.yaml").write_text("invalid: [configuration")
    output = tmp_path / "preservation.tgz"
    preserve_readable_project(original, ["sftpwarden.yaml", "users.yaml", "host_keys"], output)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(output, "r:gz") as archive:
        headers = extract_private_archive(archive, extracted, private_staging=True)
    record = json.loads((extracted / "preservation.json").read_text())
    assert record["complete_backup"] is False
    assert record["members"]["users.yaml"]["status"] == "missing"
    assert record["members"]["host_keys/synthetic-key"]["status"] == "preserved"
    assert (
        original / "host_keys" / "synthetic-key"
    ).read_bytes() == b"synthetic preservation sentinel"
    with pytest.raises(RuntimeError, match="manifest"):
        validate_backup_manifest(extracted, headers)


def test_preservation_does_not_follow_linked_material(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("must not be copied")
    try:
        (original / "users.yaml").symlink_to(outside)
    except OSError:
        pytest.skip("Host does not allow creating a test symlink")
    output = tmp_path / "preservation.tgz"
    preserve_readable_project(original, ["users.yaml"], output)
    with tarfile.open(output, "r:gz") as archive:
        assert "users.yaml" not in archive.getnames()
        record = json.load(archive.extractfile("preservation.json"))
    assert record["members"]["users.yaml"]["status"] == "unreadable_or_unsafe_path"


@pytest.mark.parametrize(
    "problem",
    ["inside", "inventory", "bytes", "metadata", "reserved", "directory", "file", "duplicate"],
)
def test_preservation_bounds_and_omissions_are_explicit(tmp_path, monkeypatch, problem):
    root = tmp_path / "original"
    root.mkdir()
    (root / "file").write_bytes(b"retained")
    (root / "directory").mkdir()
    output = tmp_path / "preserved.tgz"
    entries = ["file", "directory"]
    if problem == "inside":
        output = root / "recursive.tgz"
    elif problem == "inventory":
        monkeypatch.setattr(preserve_damaged, "MAX_ARCHIVE_MEMBERS", 1)
    elif problem == "bytes":
        monkeypatch.setattr(preserve_damaged, "MAX_ARCHIVE_BYTES", 1)
    elif problem == "metadata":
        monkeypatch.setattr(preserve_damaged, "MAX_METADATA_BYTES", 1)
    elif problem == "reserved":
        entries = ["preservation.json"]
    elif problem == "duplicate":
        entries = ["file", "file"]
    elif problem == "directory":
        original = Path.iterdir

        def iterdir(path):
            if path == root / "directory":
                raise PermissionError("unreadable directory")
            return original(path)

        monkeypatch.setattr(Path, "iterdir", iterdir)
    elif problem == "file":
        original = preserve_damaged.os.open

        def open_file(path, *args, **kwargs):
            if Path(path) == root / "file":
                raise PermissionError("unreadable file")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(preserve_damaged.os, "open", open_file)
    if problem in {"inside", "metadata"}:
        with pytest.raises(RuntimeError):
            preserve_readable_project(root, entries, output)
        assert not output.exists()
    else:
        preserve_readable_project(root, entries, output)
        with tarfile.open(output) as archive:
            record = json.load(archive.extractfile("preservation.json"))
            assert not record["complete_backup"]
            statuses = {name: member["status"] for name, member in record["members"].items()}
            expected = {
                "inventory": ("limit", "inventory_limit"),
                "bytes": ("file", "size_limit"),
                "reserved": ("preservation.json", "unreadable_or_unsafe_path"),
                "directory": ("directory", "unreadable_directory"),
                "file": ("file", "unreadable_or_changed_file"),
                "duplicate": ("file", "preserved"),
            }
            name, status = expected[problem]
            assert statuses[name] == status
            assert archive.getnames().count("file") <= 1
    assert (root / "file").read_bytes() == b"retained"
