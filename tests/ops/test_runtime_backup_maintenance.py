"""Failure paths must preserve previous output and the previous operational state."""

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import ProviderType, default_project_config, load_config
from sftpwarden.services import backup_runtime
from sftpwarden.services.backup import create_backup
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("failure", [None, "capture", "stop", "resume"])
def test_maintenance_resumes_only_previously_running_runtime(
    local_project_factory,
    monkeypatch,
    running,
    failure,
):
    root, entry = local_project_factory()
    output = root.parent / "backup.tar.gz"
    output.write_bytes(b"previous")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "inspect":
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    [
                        {
                            "Id": "owned-container",
                            "Config": {
                                "Labels": {"com.docker.compose.project.working_dir": str(root)}
                            },
                            "State": {"Running": running},
                        }
                    ]
                ),
                "",
            )
        if args[1] == "cp":
            from pathlib import Path

            Path(args[-1]).write_text(
                json.dumps(
                    {
                        "format": 1,
                        "config": load_config(entry.config).model_dump(mode="json"),
                    }
                )
            )
        if args[1] == "stop" and failure == "stop":
            raise RuntimeError("simulated stop failure")
        return subprocess.CompletedProcess(args, 0, "", "")

    def execute(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["container", "inspect"]:
            helper = args[-1]
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    [{"Id": "owned-helper", "Config": {"Labels": {"sftpwarden.capture": helper}}}]
                ).encode(),
                b"",
            )
        if args[1] == "run":
            kwargs["stdout"].write(b"complete capture")
            return subprocess.CompletedProcess(args, int(failure == "capture"), b"", b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def resume(container):
        calls.append(["resume", container])
        if failure == "resume":
            raise RuntimeError("simulated resumption failure")

    monkeypatch.setattr(backup_runtime, "_run", run)
    monkeypatch.setattr(backup_runtime, "_resume", resume)
    monkeypatch.setattr(backup_runtime.subprocess, "run", execute)
    fails = failure == "capture" or running and failure in {"stop", "resume"}
    if fails:
        with pytest.raises(RuntimeError):
            backup_runtime.create_runtime_backup(entry, output, include_data=True, dry_run=False)
        assert output.read_bytes() == b"previous"
    else:
        backup_runtime.create_runtime_backup(entry, output, include_data=True, dry_run=False)
        assert output.read_bytes() == b"complete capture"
    assert (["resume", "owned-container"] in calls) is running
    # A failed stop allocates no helper. Every attempted capture still cleans it up.
    allocated_helper = not (running and failure == "stop")
    assert any(args[:2] == ["docker", "rm"] for args in calls) is allocated_helper


def test_runtime_material_cannot_be_silently_backed_up_as_host_files(local_project_factory):
    root, entry = local_project_factory()
    (root / "state").mkdir()
    (root / "state" / "state.json").write_text('{"version": 2, "users": {}}')
    output = root.parent / "unsafe.tar.gz"
    with pytest.raises(RuntimeError, match="explicit --maintenance"):
        create_backup(context_name=entry.name, output=str(output))
    assert not output.exists()


@pytest.mark.parametrize("inspection", ["absent", "daemon-failure", "other-owner"])
def test_capture_cleanup_requires_confirmed_absence_or_ownership(monkeypatch, inspection):
    calls = []

    def inspect(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0 if inspection == "other-owner" else 1,
            json.dumps(
                [
                    {
                        "Id": "unrelated",
                        "Config": {"Labels": {"sftpwarden.capture": "other-operation"}},
                    }
                ]
            ).encode(),
            b"private daemon diagnostic",
        )

    def command(args, **kwargs):
        calls.append(args)
        assert args[:3] == ["docker", "container", "ls"]
        if inspection == "daemon-failure":
            raise OSError("daemon unavailable")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(backup_runtime.subprocess, "run", inspect)
    monkeypatch.setattr(backup_runtime, "_run", command)
    if inspection == "absent":
        backup_runtime.cleanup_capture_helper("owned-name")
        assert calls
    else:
        with pytest.raises(RuntimeError, match="confirm cleanup") as error:
            backup_runtime.cleanup_capture_helper("owned-name")
        assert "private daemon diagnostic" not in str(error.value)
    assert not any("rm" in args for args in calls)


@pytest.mark.parametrize("pending", ["restore-metadata.json", "restore-in-progress.json"])
def test_capture_refuses_an_unfinished_runtime_restore(tmp_path, pending):
    from sftpwarden.config import default_project_config
    from sftpwarden.runtime.backup import capture
    from sftpwarden.runtime.core import RuntimeState

    config = default_project_config("recovery")
    config.server.state_dir = str(tmp_path / "state")
    RuntimeState(users={}).save(tmp_path / "state" / "state.json")
    (tmp_path / "state" / pending).write_text('{"format": 1}')
    applied = tmp_path / "applied.json"
    applied.write_text(json.dumps({"format": 1, "config": config.model_dump(mode="json")}))
    with pytest.raises(RuntimeError, match="pending recovery"):
        capture(applied, include_data=False)


@pytest.mark.parametrize("failure", [None, "exit", "launch", "timeout"])
def test_bounded_runtime_commands_report_failures_privately(monkeypatch, failure):
    def run(args, **kwargs):
        assert kwargs["timeout"] == 30
        if failure == "launch":
            raise OSError("private-sentinel")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(args, int(failure == "exit"), "safe", "private-sentinel")

    monkeypatch.setattr(backup_runtime.subprocess, "run", run)
    if failure:
        with pytest.raises(RuntimeError) as caught:
            backup_runtime._run(["docker", "inspect"])
        assert "private-sentinel" not in str(caught.value)
    else:
        assert backup_runtime._run(["docker", "inspect"]).stdout == "safe"


@pytest.mark.parametrize("outcome", ["healthy", "stopped", "timeout"])
def test_resume_requires_health_and_bounds_readiness_wait(monkeypatch, outcome):
    calls = []
    states = iter(
        [{"Running": True}, {"Running": outcome != "stopped", "Health": {"Status": "healthy"}}]
    )
    ticks = iter([0, 1, 95 if outcome == "timeout" else 2])
    monkeypatch.setattr(backup_runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(backup_runtime.time, "sleep", lambda seconds: None)

    def run(args):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(next(states)) if args[1] == "inspect" else "")

    monkeypatch.setattr(backup_runtime, "_run", run)
    if outcome == "healthy":
        backup_runtime._resume("selected")
    else:
        with pytest.raises(RuntimeError, match="healthy"):
            backup_runtime._resume("selected")
    assert calls[0] == ["docker", "start", "selected"]


@pytest.mark.parametrize("problem", ["owner", "paused", "format"])
def test_runtime_inspection_failure_does_not_enter_maintenance(
    local_project_factory, monkeypatch, problem
):
    root, entry = local_project_factory()
    calls = []

    def run(args):
        calls.append(args)
        if args[1] == "inspect":
            return SimpleNamespace(
                stdout=json.dumps(
                    [
                        {
                            "Id": "selected",
                            "State": {"Paused": problem == "paused"},
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project.working_dir": str(
                                        root / "other" if problem == "owner" else root
                                    )
                                }
                            },
                        }
                    ]
                )
            )
        assert args[1] == "cp"
        Path(args[-1]).write_text('{"format":99}')

    monkeypatch.setattr(backup_runtime, "_run", run)
    with pytest.raises(RuntimeError), backup_runtime.inspect_runtime(entry):
        pytest.fail("unsafe runtime selected")
    assert not any("stop" in args for args in calls)


@pytest.mark.parametrize("network", ["", "none", "container:other", "selected-network"])
def test_external_capture_requires_its_runtime_network_and_cleans_on_timeout(
    tmp_path, monkeypatch, network
):
    config = default_project_config("test", ProviderType.POSTGRESQL, dsn="postgresql://db/test")
    runtime = backup_runtime.RuntimeCapture(
        "runtime", tmp_path / "applied.json", config, {"HostConfig": {"NetworkMode": network}}
    )
    runtime.applied.write_text("{}")
    cleaned = []

    def run(args, **kwargs):
        assert args[args.index("--network") + 1] == network
        raise subprocess.TimeoutExpired(args, 300)

    monkeypatch.setattr(backup_runtime.subprocess, "run", run)
    monkeypatch.setattr(backup_runtime, "cleanup_capture_helper", cleaned.append)
    with pytest.raises(RuntimeError):
        backup_runtime.capture_runtime(runtime, io.BytesIO(), include_data=False)
    assert bool(cleaned) == (network == "selected-network")
