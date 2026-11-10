"""Remote sources must never fall back to a local-sync project's stale files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sftpwarden.contexts import ContextEntry, ContextType, RemoteEndpoint
from sftpwarden.services import backup, backup_remote, restore_remote
from sftpwarden.services.restore_remote import fresh_restore_script
from sftpwarden.utils.errors import RuntimeError


@pytest.fixture
def remote_entry(tmp_path: Path) -> ContextEntry:
    return ContextEntry(
        name="remote",
        type=ContextType.REMOTE,
        root=str(tmp_path),
        config=str(tmp_path / "missing-local-config.yaml"),
        storage="local-sync",
        remote=RemoteEndpoint(
            host="synthetic.invalid",
            user="operator",
            remote_root="~/server's files",
            remote_config="~/server's files/sftpwarden.yaml",
        ),
    )


def test_local_sync_backup_selects_remote_runtime(monkeypatch, tmp_path, remote_entry):
    monkeypatch.setattr(backup, "resolve_context", lambda **kwargs: remote_entry)
    result = backup.create_backup(
        context_name="remote",
        output=str(tmp_path / "out.tgz"),
        include_data=True,
        maintenance=True,
        dry_run=True,
    )
    assert "remote:data" in result.entries
    assert not result.path.exists()
    assert not Path(remote_entry.config).exists()


def test_remote_backup_requires_maintenance_before_ssh(monkeypatch, tmp_path, remote_entry):
    monkeypatch.setattr(backup_remote.subprocess, "run", lambda *a, **k: pytest.fail("SSH"))
    with pytest.raises(RuntimeError, match="maintenance"):
        backup_remote.create_remote_runtime_backup(
            remote_entry,
            tmp_path / "out.tgz",
            include_data=False,
            dry_run=False,
            maintenance=False,
        )


@pytest.mark.parametrize("failure", ["cleanup", "timeout", "invalid_archive"])
def test_remote_capture_failure_preserves_previous_output(
    monkeypatch,
    tmp_path,
    remote_entry,
    failure,
):
    output = tmp_path / "existing.tgz"
    output.write_bytes(b"previous recovery point")

    def run(command, **kwargs):
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 540
        kwargs["stdout"].write(b"incomplete or invalid archive")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 540)
        return subprocess.CompletedProcess(command, 1 if failure == "cleanup" else 0)

    monkeypatch.setattr(backup_remote.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        backup_remote.create_remote_runtime_backup(
            remote_entry,
            output,
            include_data=True,
            dry_run=False,
            maintenance=True,
        )
    assert output.read_bytes() == b"previous recovery point"


def test_capture_script_uses_actual_mounts_and_resumes_before_unlock(remote_entry):
    script = backup_remote.capture_script(remote_entry, include_data=True)
    assert "--volumes-from" in script
    assert "--all --quiet sftpwarden" in script
    assert "applied-config.json" in script
    assert script.index("resume=1") < script.index("docker stop")
    assert "State.ExitCode" in script
    assert "State.Health.Status" in script
    assert "mkdir .sftpwarden-operation.lock" in script
    assert "remote-compose-runtime --include-data" in script
    assert str(remote_entry.root) not in script


def test_fresh_remote_plan_does_not_create_destination(remote_entry):
    script = fresh_restore_script(remote_entry, include_data=True, dry_run=True)
    assert "mkdir" not in script
    assert "docker run" not in script
    assert "test ! -L" in script
    assert "find" in script
    assert 'cd -- "$HOME"' in script


@pytest.mark.parametrize("path", ["/", "~", "/safe/../danger"])
def test_remote_recovery_refuses_nondedicated_roots(remote_entry, path):
    remote_entry.remote.remote_root = path
    with pytest.raises(RuntimeError, match="dedicated"):
        fresh_restore_script(remote_entry, include_data=False, dry_run=False)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("outcome", ["success", "exit", "timeout"])
def test_remote_restore_stream_is_private_and_uncertain_results_are_failures(
    tmp_path, remote_entry, monkeypatch, dry_run, outcome
):
    archive = tmp_path / "input.tgz"
    archive.write_bytes(b"synthetic recovery stream")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == (60 if dry_run else 540)
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        if dry_run:
            assert kwargs["stdin"] == subprocess.DEVNULL
        else:
            assert kwargs["stdin"].read() == archive.read_bytes()
            assert "--protected-external" in args[-1]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, 540, stderr=b"private-sentinel")
        return subprocess.CompletedProcess(args, int(outcome == "exit"))

    monkeypatch.setattr(restore_remote.subprocess, "run", run)
    if outcome == "success":
        restore_remote.restore_fresh_remote(
            remote_entry, archive, include_data=True, dry_run=dry_run, protected_external=True
        )
    else:
        with pytest.raises(RuntimeError) as caught:
            restore_remote.restore_fresh_remote(
                remote_entry, archive, include_data=True, dry_run=dry_run, protected_external=True
            )
        assert "private-sentinel" not in str(caught.value)
    assert len(calls) == 1


def test_remote_restore_requires_matching_configuration(remote_entry):
    remote_entry.remote.remote_config = "/different/config.yaml"
    with pytest.raises(RuntimeError, match="sftpwarden.yaml"):
        fresh_restore_script(remote_entry, include_data=False, dry_run=False)
    remote_entry.remote = None
    with pytest.raises(RuntimeError, match="endpoint"):
        fresh_restore_script(remote_entry, include_data=False, dry_run=False)
