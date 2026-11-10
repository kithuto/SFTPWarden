"""Ownership recovery validates the entire target before privileged effects."""

import hashlib
import json
import os
from contextlib import ExitStack
from types import SimpleNamespace

import pytest

from sftpwarden.config import default_project_config
from sftpwarden.runtime import recovery
from sftpwarden.runtime.core import RuntimeState, RuntimeUserState
from sftpwarden.utils.errors import RuntimeError


def recovery_case(tmp_path):
    config = default_project_config("recovery")
    config.server.data_dir = str(tmp_path / "data")
    config.server.state_dir = str(tmp_path / "state")
    config.server.host_keys_dir = str(tmp_path / "host_keys")
    state = RuntimeState(users={"alice": RuntimeUserState(uid=2001, gid=2001)})
    state.save(tmp_path / "state" / "state.json")
    path = tmp_path / "data" / "alice" / "upload" / "file.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    members = {
        "data/alice/upload/file.txt": {
            "type": "file",
            "size": 8,
            "mode": 0o600,
            "uid": 2001,
            "gid": 2001,
            "sha256": hashlib.sha256(b"original").hexdigest(),
        }
    }
    return config, state, path, members


@pytest.mark.parametrize("field,value", [("uid", 999), ("gid", 987), ("mode", 0o4600)])
def test_recovery_rejects_unmapped_owners_and_special_modes(tmp_path, field, value):
    config, state, _path, members = recovery_case(tmp_path)
    members["data/alice/upload/file.txt"][field] = value
    with pytest.raises((RuntimeError, ValueError)):
        recovery.validated_metadata(members, state, config)


@pytest.mark.skipif(os.name == "nt", reason="Linux descriptor-based runtime boundary")
@pytest.mark.parametrize("damage", ["symlink", "content"])
def test_recovery_preflight_has_no_ownership_effects(tmp_path, monkeypatch, damage):
    config, _state, path, members = recovery_case(tmp_path)
    recovery.write_recovery_record(tmp_path, members, config)
    if damage == "symlink":
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"original")
        path.unlink()
        path.symlink_to(outside)
    else:
        path.write_bytes(b"tampered")
    effects = []
    monkeypatch.setattr(recovery.os, "fchown", lambda *args: effects.append(args))
    with pytest.raises((RuntimeError, OSError)):
        recovery.apply_recovery_metadata(config)
    assert not effects
    assert (tmp_path / "state" / recovery.RECOVERY_FILE).exists()
    status = json.loads((tmp_path / "state" / recovery.STATUS_FILE).read_text())
    assert status["phase"] == "validation_failed"
    assert status["completed_members"] == 0


@pytest.mark.skipif(os.name == "nt", reason="Linux descriptor-based runtime boundary")
def test_interrupted_metadata_application_retains_retry_record(tmp_path, monkeypatch):
    config, _state, _path, members = recovery_case(tmp_path)
    recovery.write_recovery_record(tmp_path, members, config)

    def fail(*args):
        raise OSError("interrupted ownership change")

    monkeypatch.setattr(recovery.os, "fchown", fail)
    with pytest.raises(OSError, match="interrupted ownership"):
        recovery.apply_recovery_metadata(config)
    assert (tmp_path / "state" / recovery.RECOVERY_FILE).exists()
    status = json.loads((tmp_path / "state" / recovery.STATUS_FILE).read_text())
    assert status == {
        "format": 1,
        "completed_members": 0,
        "pending_members": 1,
        "phase": "retry_required",
    }


def test_incomplete_file_restoration_prevents_runtime_start(tmp_path):
    config, _state, _path, _members = recovery_case(tmp_path)
    blocker = tmp_path / "state" / recovery.BLOCK_FILE
    blocker.write_text('{"format":1}')
    with pytest.raises(RuntimeError, match="incomplete"):
        recovery.apply_recovery_metadata(config)
    assert blocker.exists()


@pytest.mark.skipif(os.name == "nt", reason="Linux runtime activation boundary")
def test_existing_container_cannot_activate_restored_files(tmp_path, monkeypatch):
    config, _state, _path, members = recovery_case(tmp_path)
    recovery.write_recovery_record(tmp_path, members, config, requires_recreate=True)
    applied = tmp_path / "applied.json"
    applied.write_text("{}")
    monkeypatch.setattr(recovery, "APPLIED_CONFIG_PATH", applied)
    effects = []
    monkeypatch.setattr(recovery.os, "fchown", lambda *args: effects.append(args))
    with pytest.raises(RuntimeError, match="new container"):
        recovery.apply_recovery_metadata(config)
    assert not effects
    assert (tmp_path / "state" / recovery.RECOVERY_FILE).exists()


@pytest.mark.parametrize("damage", ["digest", "directory-size", "destination", "journal"])
def test_recovery_metadata_rejects_invalid_content_or_operational_markers(tmp_path, damage):
    config, state, _, members = recovery_case(tmp_path)
    member = members.pop("data/alice/upload/file.txt")
    name = "data/alice/upload/file.txt"
    if damage == "digest":
        member["sha256"] = "invalid"
    elif damage == "directory-size":
        member["type"] = "directory"
    elif damage == "destination":
        name = "unmanaged/file"
    else:
        name = "state/" + recovery.STATUS_FILE
    with pytest.raises(RuntimeError):
        recovery.validated_metadata({name: member}, state, config)


def test_public_keys_and_chroot_directories_receive_safe_metadata(tmp_path):
    config, state, _, members = recovery_case(tmp_path)
    member = next(iter(members.values()))
    values = recovery.validated_metadata(
        {
            "host_keys/key.pub": member,
            "data/alice": {**member, "type": "directory", "size": 0, "sha256": ""},
        },
        state,
        config,
    )
    assert (values["host_keys/key.pub"].uid, values["host_keys/key.pub"].mode) == (0, 0o644)
    assert (values["data/alice"].uid, values["data/alice"].mode) == (
        0,
        int(config.isolation.root_permissions, 8),
    )


def test_empty_metadata_needs_no_state_but_nonempty_metadata_does(tmp_path):
    config = default_project_config("test")
    recovery.write_recovery_record(tmp_path, {}, config)
    assert not list(tmp_path.iterdir())
    with pytest.raises(RuntimeError, match="retained state"):
        recovery.write_recovery_record(tmp_path, {"data": {}}, config)


@pytest.mark.parametrize("applied", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_recovery_entrypoint_selects_frozen_config_and_keeps_errors_private(
    tmp_path, monkeypatch, capsys, applied, failure
):
    config = default_project_config("test")
    path = tmp_path / "applied"
    if applied:
        path.touch()
    monkeypatch.setattr(recovery, "APPLIED_CONFIG_PATH", path)
    monkeypatch.setenv("SFTPWARDEN_CONFIG", "selected.yaml")
    monkeypatch.setattr(
        recovery, "load_runtime_config" if applied else "load_config", lambda path: config
    )
    calls = []

    def apply(selected):
        calls.append(selected)
        if failure:
            raise OSError("private-sentinel")

    monkeypatch.setattr(recovery, "apply_recovery_metadata", apply)
    assert recovery.main() == int(failure)
    assert calls == [config]
    output = capsys.readouterr()
    assert not output.out and "private-sentinel" not in output.err
    assert bool(output.err) == failure


@pytest.mark.skipif(os.name == "nt", reason="Linux descriptor-relative runtime application")
@pytest.mark.parametrize("retained", [False, True])
def test_recovery_completes_only_after_metadata_is_observed_on_disk(
    tmp_path, monkeypatch, retained
):
    config, _, path, members = recovery_case(tmp_path)
    recovery.write_recovery_record(tmp_path, members, config)
    original_stat = os.fstat
    observed = set()

    def own(fd, uid, gid):
        observed.add(fd)

    def info(fd):
        result = original_stat(fd)
        if fd not in observed:
            return result
        return SimpleNamespace(st_uid=2001 if retained else 0, st_gid=2001, st_mode=result.st_mode)

    monkeypatch.setattr(recovery.os, "fchown", own)
    monkeypatch.setattr(recovery.os, "fstat", info)
    if retained:
        recovery.apply_recovery_metadata(config)
        assert not (tmp_path / "state" / recovery.RECOVERY_FILE).exists()
    else:
        with pytest.raises(RuntimeError, match="did not retain"):
            recovery.apply_recovery_metadata(config)
        assert (tmp_path / "state" / recovery.RECOVERY_FILE).exists()
    status = json.loads((tmp_path / "state" / recovery.STATUS_FILE).read_text())
    assert status["phase"] == ("metadata_applied" if retained else "retry_required")
    assert path.read_bytes() == b"original"


@pytest.mark.skipif(os.name == "nt", reason="Linux descriptor-relative runtime verification")
def test_recovery_verifies_directory_type_and_file_size(tmp_path):
    config, state, path, members = recovery_case(tmp_path)
    file = recovery.RecoveryMember.model_validate(next(iter(members.values())))
    directory = file.model_copy(update={"type": "directory", "size": 0, "sha256": ""})
    fd = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(RuntimeError, match="directory was replaced"):
            recovery._verify_payload(fd, directory)
        with pytest.raises(RuntimeError, match="file was replaced"):
            recovery._verify_payload(fd, file.model_copy(update={"size": 99}))
    finally:
        os.close(fd)
    root_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with ExitStack() as stack:
            assert recovery._open_member({"data": root_fd}, "data", directory, stack) == root_fd
            recovery._verify_payload(root_fd, directory)
    finally:
        os.close(root_fd)
