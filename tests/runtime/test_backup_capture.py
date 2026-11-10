"""Capture preconditions, connection isolation and ownership restoration on failures."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import DeployTarget, ProviderType, default_project_config, load_config
from sftpwarden.runtime import backup
from sftpwarden.runtime.core import RuntimeState, RuntimeUserState
from sftpwarden.runtime.recovery import BLOCK_FILE, RECOVERY_FILE
from sftpwarden.utils.errors import RuntimeError


@pytest.fixture
def capture_case(tmp_path, monkeypatch):
    config = default_project_config("capture")
    for name in ("state", "host_keys", "data"):
        path = tmp_path / name
        path.mkdir()
        setattr(
            config.server,
            {"state": "state_dir", "host_keys": "host_keys_dir", "data": "data_dir"}[name],
            str(path),
        )
    config.provider.path = str(tmp_path / "users.yaml")
    Path(config.provider.path).write_text("schema_version: 2\nusers: []\n")
    state = RuntimeState({"alice": RuntimeUserState(2101, 2102)}, fingerprint="applied")
    state_path = tmp_path / "state/state.json"
    state.save(state_path)
    applied = tmp_path / "applied.json"

    def save():
        applied.write_text(json.dumps({"format": 1, "config": config.model_dump(mode="json")}))

    save()
    monkeypatch.setattr(backup.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    return SimpleNamespace(
        config=config, state=state, state_path=state_path, applied=applied, save=save
    )


@pytest.mark.parametrize(
    "problem",
    ["format", "missing-state", "pending", "fingerprint", BLOCK_FILE, RECOVERY_FILE, "connection"],
)
def test_incomplete_runtime_never_emits_a_successful_archive(capture_case, monkeypatch, problem):
    case = capture_case
    if problem == "format":
        case.applied.write_text('{"format":99}')
    elif problem == "missing-state":
        case.state_path.unlink()
    elif problem in ("pending", "fingerprint"):
        case.state.pending = problem == "pending"
        case.state.fingerprint = "applied" if problem == "pending" else None
        case.state.save(case.state_path)
    elif problem == "connection":
        case.config.provider.type = ProviderType.POSTGRESQL
        case.config.provider.dsn = "postgresql://db/test"
        case.save()
    else:
        (case.state_path.parent / problem).write_text("unfinished")
    monkeypatch.setattr(
        backup, "write_backup_archive", lambda **kwargs: pytest.fail("incomplete capture published")
    )
    with pytest.raises(RuntimeError):
        backup.capture(case.applied, include_data=True)


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.SQLITE, ProviderType.POSTGRESQL])
@pytest.mark.parametrize("include_data", [False, True])
def test_capture_uses_applied_policy_and_selected_connection(
    capture_case, tmp_path, monkeypatch, kind, include_data
):
    case = capture_case
    case.config.provider.type = kind
    case.config.provider.dsn = "${CAPTURE_TEST_DSN}" if kind == ProviderType.POSTGRESQL else None
    case.save()
    info = tmp_path / "container.json"
    info.write_text(
        json.dumps(
            {
                "Config": {
                    "Env": [
                        "CAPTURE_TEST_DSN=postgresql://db/test",
                        "PGSSLMODE=require",
                        "UNRELATED=not-copied",
                        "bare",
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("CAPTURE_TEST_DSN", "old")
    monkeypatch.setenv("PGSSLMODE", "old")
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "old")
    monkeypatch.delenv("UNRELATED", raising=False)
    copied = []

    def snapshot(source, destination):
        copied.append(source)
        destination.write_bytes(b"snapshot")

    monkeypatch.setattr(backup, "snapshot_sqlite", snapshot)
    archives = []

    def write(**kwargs):
        archived = load_config(kwargs["root"] / "sftpwarden.yaml")
        assert archived.deploy.target == DeployTarget.COMPOSE and not archived.remote.enabled
        assert kwargs["applied_fingerprint"] == "applied"
        assert kwargs["include_data"] == include_data
        assert ("data" in kwargs["sources"]) == include_data
        assert kwargs["topology"] == "kubernetes-runtime"
        if kind == ProviderType.POSTGRESQL:
            assert backup.os.environ["CAPTURE_TEST_DSN"] == "postgresql://db/test"
            assert backup.os.environ["PGSSLMODE"] == "require"
            assert "PGCONNECT_TIMEOUT" not in backup.os.environ
            assert "UNRELATED" not in backup.os.environ
        else:
            assert archived.provider.path == "/etc/sftpwarden/provider-data/users.yaml"
            assert (kwargs["root"] / "provider/users.yaml").read_bytes() == (
                b"snapshot"
                if kind == ProviderType.SQLITE
                else Path(case.config.provider.path).read_bytes()
            )
        archives.append(kwargs)

    monkeypatch.setattr(backup, "write_backup_archive", write)
    backup.capture(
        case.applied, include_data=include_data, topology="kubernetes-runtime", container_info=info
    )
    assert len(archives) == 1
    assert bool(copied) == (kind == ProviderType.SQLITE)


@pytest.mark.parametrize("failure", [None, PermissionError, OSError])
def test_capture_restores_helper_identity_after_ownership_observation(
    capture_case, monkeypatch, failure
):
    calls = []
    for name in ("setgroups", "setegid", "seteuid"):
        monkeypatch.setattr(
            backup.os, name, lambda value, name=name: calls.append((name, value)), raising=False
        )
    monkeypatch.setattr(backup.os, "getgroups", lambda: [7, 8], raising=False)

    def observe():
        if failure and calls[-1] == ("seteuid", 2101):
            raise failure("observation failed")
        return SimpleNamespace(st_uid=0, st_gid=0)

    def write(**kwargs):
        assert kwargs["ownership"](SimpleNamespace(lstat=observe), "data/alice/upload/file") == (
            0,
            0,
        )
        assert kwargs["ownership"](SimpleNamespace(lstat=observe), "state/state.json") == (0, 0)

    monkeypatch.setattr(backup, "write_backup_archive", write)
    if failure is OSError:
        with pytest.raises(OSError, match="observation"):
            backup.capture(capture_case.applied, include_data=True)
    else:
        backup.capture(capture_case.applied, include_data=True)
    assert calls == [
        ("setgroups", []),
        ("setegid", 2102),
        ("seteuid", 2101),
        ("seteuid", 0),
        ("setegid", 0),
        ("setgroups", [7, 8]),
    ]


@pytest.mark.parametrize("failure", [False, True])
def test_capture_entry_point_never_mixes_diagnostics_into_binary_output(
    monkeypatch, capsys, failure
):
    monkeypatch.setattr(
        backup.sys, "argv", ["backup", "--applied", "applied.json", "--include-data"]
    )
    calls = []

    def capture(path, **kwargs):
        calls.append((path, kwargs))
        if failure:
            raise OSError("private-sentinel")

    monkeypatch.setattr(backup, "capture", capture)
    assert backup.main() == int(failure)
    assert calls[0][0] == Path("applied.json") and calls[0][1]["include_data"]
    output = capsys.readouterr()
    assert not output.out and "private-sentinel" not in output.err
    assert bool(output.err) == failure
