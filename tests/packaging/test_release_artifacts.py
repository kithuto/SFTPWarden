"""Artifact promotion must detect any byte or provenance change."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def artifacts():
    spec = importlib.util.spec_from_file_location(
        "release_artifacts", Path("tools/release_artifacts.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("change", ["changed", "missing", "extra", "commit"])
def test_candidate_verification_rejects_changed_inputs(artifacts, tmp_path, change):
    artifact = tmp_path / "package.whl"
    artifact.write_bytes(b"candidate bytes")
    commit = "a" * 40
    artifacts.record(tmp_path, commit, "1.3.1")
    assert artifacts.verify(tmp_path, commit)["version"] == "1.3.1"
    if change == "changed":
        artifact.write_bytes(b"different bytes")
    elif change == "missing":
        artifact.unlink()
    elif change == "extra":
        (tmp_path / "other.whl").write_bytes(b"unexpected")
    else:
        commit = "b" * 40
    with pytest.raises(ValueError):
        artifacts.verify(tmp_path, commit)


def test_candidate_record_cannot_overwrite_existing_provenance(artifacts, tmp_path):
    (tmp_path / "package.whl").write_bytes(b"candidate bytes")
    artifacts.record(tmp_path, "a" * 40, "1.3.1")
    with pytest.raises(FileExistsError):
        artifacts.record(tmp_path, "b" * 40, "1.3.1")
