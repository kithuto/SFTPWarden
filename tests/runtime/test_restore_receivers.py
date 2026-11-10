"""Stream preflight and interruption guarantees for disposable recovery helpers."""

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import default_project_config
from sftpwarden.runtime import restore, restore_cluster
from sftpwarden.runtime.recovery import BLOCK_FILE
from sftpwarden.utils.errors import RuntimeError


@pytest.fixture
def streamed_archive(monkeypatch):
    members = {
        "host_keys/key": b"synthetic recovery material",
        "state/state.json": b"retained identity",
        "provider/users.yaml": b"schema_version: 2\nusers: []\n",
        "data/alice/upload/payload": b"preserved user data",
    }
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        for name, payload in members.items():
            header = tarfile.TarInfo(name)
            header.size = len(payload)
            header.mode = 0o600
            archive.addfile(header, io.BytesIO(payload))
    packed = content.getvalue()
    entries = ["host_keys", "state", "provider", "data", *members]

    def feed(module, value=packed):
        monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(value)))

    return SimpleNamespace(content=packed, entries=entries, members=members, feed=feed)


@pytest.mark.parametrize("include_data", [False, True])
def test_empty_receiver_validates_stream_before_publication(
    tmp_path, monkeypatch, streamed_archive, include_data
):
    destination = tmp_path / "destination"
    root = destination / "restored"
    monkeypatch.setattr(
        restore, "Path", lambda value: destination if str(value) == "/destination" else Path(value)
    )
    manifest = SimpleNamespace(entries=streamed_archive.entries)
    calls = []

    def validate(source, headers):
        assert (source / "state/state.json").read_bytes() == b"retained identity"
        calls.append("validated")
        return manifest

    def inspect(path, selected, **kwargs):
        assert calls == ["validated"] and path == str(root) and selected is manifest
        assert kwargs["protected_external"] is True
        calls.append("inspected")
        return "empty-target"

    def publish(target, source, entries, selected, **kwargs):
        assert calls == ["validated", "inspected"] and target == "empty-target"
        assert selected is manifest and kwargs["project_owner"] == (1001, 1002)
        assert ("data/alice/upload/payload" in entries) == include_data
        assert ("data" in entries) == include_data
        calls.append("published")

    monkeypatch.setattr(restore, "validate_backup_manifest", validate)
    monkeypatch.setattr(restore, "inspect_empty_target", inspect)
    monkeypatch.setattr(restore, "publish_fresh_files", publish)
    streamed_archive.feed(restore)
    restore.restore_empty(
        root, include_data=include_data, owner=(1001, 1002), protected_external=True
    )
    assert calls == ["validated", "inspected", "published"]


@pytest.mark.parametrize(
    "root,owner,message",
    [
        ("/outside/restored", (1, 1), "dedicated"),
        ("/destination/restored", (-1, 1), "owner"),
        ("/destination/restored", (1, 2**32 - 1), "owner"),
    ],
)
def test_empty_receiver_rejects_unsafe_destination_before_reading(
    monkeypatch, root, owner, message
):
    monkeypatch.setattr(restore.sys, "stdin", None)
    with pytest.raises(RuntimeError, match=message):
        restore.restore_empty(Path(root), include_data=True, owner=owner)


def test_empty_receiver_rejects_oversize_and_corrupt_streams(
    tmp_path, monkeypatch, streamed_archive
):
    destination = tmp_path / "destination"
    monkeypatch.setattr(
        restore, "Path", lambda value: destination if str(value) == "/destination" else Path(value)
    )
    monkeypatch.setattr(
        restore,
        "inspect_empty_target",
        lambda *a, **k: pytest.fail("Unverified stream reached destination"),
    )
    monkeypatch.setattr(restore, "MAX_ARCHIVE_BYTES", len(streamed_archive.content) - 1)
    streamed_archive.feed(restore)
    with pytest.raises(RuntimeError, match="size"):
        restore.restore_empty(destination / "restored", include_data=True, owner=(1, 1))
    streamed_archive.feed(restore, b"not an archive")
    with pytest.raises(tarfile.TarError):
        restore.restore_empty(destination / "restored", include_data=True, owner=(1, 1))


@pytest.fixture
def cluster_receiver(tmp_path, monkeypatch, streamed_archive):
    config = default_project_config("cluster")
    mounts = {name: tmp_path / name for name in ("state", "host_keys", "data", "provider")}
    for path in mounts.values():
        path.mkdir()
    config.server.state_dir = str(mounts["state"])
    config.server.host_keys_dir = str(mounts["host_keys"])
    config.server.data_dir = str(mounts["data"])
    config.provider.path = str(mounts["provider"] / "users.yaml")
    manifest = SimpleNamespace(config=config, entries=list(streamed_archive.entries))
    monkeypatch.setattr(restore_cluster, "validate_backup_manifest", lambda *args: manifest)
    monkeypatch.setattr(
        restore_cluster,
        "provider_local_path",
        lambda source, config: source / "provider/users.yaml",
    )
    monkeypatch.setattr(type(tmp_path), "is_mount", lambda path: path in mounts.values())
    monkeypatch.setattr(restore_cluster, "recovery_metadata", lambda *args: {"retained": True})
    monkeypatch.setattr(
        restore_cluster,
        "write_recovery_record",
        lambda source, metadata, config: (source / "state/recovery.json").write_text(
            json.dumps(metadata)
        ),
    )
    # Linux ownership application has separate real-container gates. Here the
    # boundary must occur only after every selected file is durable and unblocked.
    applied = []

    def apply(selected):
        assert selected == config
        assert not (mounts["state"] / BLOCK_FILE).exists()
        assert (mounts["state"] / "state.json").read_bytes() == b"retained identity"
        assert (mounts["host_keys"] / "key").read_bytes() == b"synthetic recovery material"
        applied.append(True)

    monkeypatch.setattr(restore_cluster, "apply_recovery_metadata", apply)
    monkeypatch.setattr(restore_cluster, "sync_directory", lambda path: None)
    streamed_archive.feed(restore_cluster)
    return SimpleNamespace(config=config, manifest=manifest, mounts=mounts, applied=applied)


@pytest.mark.parametrize("include_data", [False, True])
@pytest.mark.parametrize("external", [False, True])
def test_cluster_receiver_preserves_selected_payload_and_marks_completion(
    cluster_receiver, monkeypatch, include_data, external
):
    case = cluster_receiver
    if external:
        case.config.provider.type = "postgresql"
    restore_cluster.receive(include_data=include_data)
    assert case.applied == [True]
    assert (case.mounts["data"] / "alice/upload/payload").exists() == include_data
    assert (case.mounts["provider"] / "users.yaml").exists() != external


@pytest.mark.parametrize(
    "failure", ["missing-identity", "overlap", "not-mounted", "nonempty", "metadata"]
)
def test_cluster_preflight_failure_never_populates_empty_volumes(
    cluster_receiver, monkeypatch, failure
):
    case = cluster_receiver
    if failure == "missing-identity":
        case.manifest.entries.remove("state")
    elif failure == "overlap":
        case.config.server.host_keys_dir = case.config.server.state_dir
    elif failure == "not-mounted":
        monkeypatch.setattr(type(case.mounts["state"]), "is_mount", lambda path: False)
    elif failure == "nonempty":
        (case.mounts["data"] / "operator-file").write_text("must survive")
    else:
        monkeypatch.setattr(restore_cluster, "recovery_metadata", lambda *args: {})
    before = {
        path: path.read_bytes()
        for root in case.mounts.values()
        for path in root.rglob("*")
        if path.is_file()
    }
    with pytest.raises(RuntimeError):
        restore_cluster.receive(include_data=True)
    assert case.applied == []
    assert {
        path: path.read_bytes()
        for root in case.mounts.values()
        for path in root.rglob("*")
        if path.is_file()
    } == before


def test_cluster_copy_interruption_retains_a_blocker_and_completed_inventory(
    cluster_receiver, monkeypatch
):
    case = cluster_receiver
    original = restore_cluster.shutil.copy2

    def fail(source, target):
        if source.name == "state.json":
            raise OSError("interrupted copy")
        return original(source, target)

    monkeypatch.setattr(restore_cluster.shutil, "copy2", fail)
    with pytest.raises(OSError, match="interrupted"):
        restore_cluster.receive(include_data=True)
    marker = json.loads((case.mounts["state"] / BLOCK_FILE).read_bytes())
    assert marker == {"format": 1, "phase": "failed", "completed": ["host_keys"]}
    assert (case.mounts["host_keys"] / "key").is_file()
    assert case.applied == []


def test_cluster_metadata_failure_restores_the_startup_blocker(cluster_receiver, monkeypatch):
    def fail(config):
        raise OSError("ownership could not be applied")

    monkeypatch.setattr(restore_cluster, "apply_recovery_metadata", fail)
    with pytest.raises(OSError, match="ownership"):
        restore_cluster.receive(include_data=True)
    marker = json.loads((cluster_receiver.mounts["state"] / BLOCK_FILE).read_bytes())
    assert marker["phase"] == "failed" and marker["completed"] == ["host_keys", "state", "data"]


def test_cluster_rejects_oversized_stream_before_inspecting_claims(monkeypatch, streamed_archive):
    streamed_archive.feed(restore_cluster)
    monkeypatch.setattr(restore_cluster, "MAX_ARCHIVE_BYTES", 1)
    monkeypatch.setattr(
        restore_cluster,
        "validate_backup_manifest",
        lambda *a: pytest.fail("Oversize stream was accepted"),
    )
    with pytest.raises(RuntimeError, match="limit"):
        restore_cluster.receive(include_data=True)


@pytest.mark.parametrize("module", [restore, restore_cluster])
@pytest.mark.parametrize("fail", [False, True])
def test_receivers_report_status_without_exposing_stream_details(monkeypatch, capsys, module, fail):
    calls = []

    def receive(*args, **kwargs):
        calls.append((args, kwargs))
        if fail:
            raise OSError("private-archive-sentinel")

    if module is restore:
        arguments = [
            "restore",
            "--root",
            "/destination/new",
            "--uid",
            "1",
            "--gid",
            "2",
            "--include-data",
            "--protected-external",
        ]
        monkeypatch.setattr(module, "restore_empty", receive)
    else:
        arguments = ["restore-cluster", "--include-data"]
        monkeypatch.setattr(module, "receive", receive)
    monkeypatch.setattr(module.sys, "argv", arguments)
    assert module.main() == int(fail)
    assert len(calls) == 1 and calls[0][1]["include_data"] is True
    output = capsys.readouterr()
    assert output.out == "" and "private-archive-sentinel" not in output.err
    assert bool(output.err) == fail
