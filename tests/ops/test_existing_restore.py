"""An existing target must be inspected before safety capture or replacement."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import DeployTarget, ProviderType, load_config, write_config
from sftpwarden.contexts import ContextType
from sftpwarden.providers import provider_from_config
from sftpwarden.services import restore_existing
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.services.backup_manifest import ValidatedBackup
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.errors import RuntimeError


@pytest.fixture(autouse=True)
def isolated_safety_archives(tmp_path, monkeypatch):
    """Keep all safety archives inside the test-owned temporary directory."""
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "problem",
    [
        "remote",
        "journal",
        "config-path",
        "damaged-config",
        "deployment",
        "provider-type",
        "provider-path",
        "type-conflict",
        "child-conflict",
        "unwritable",
    ],
)
def test_existing_target_preflight_preserves_all_files(
    local_project_factory, tmp_path, monkeypatch, problem
):
    root, entry = local_project_factory()
    config = load_config(entry.config)
    manifest = ValidatedBackup(
        ["sftpwarden.yaml", "users.yaml"],
        "sftpwarden.yaml",
        config.model_copy(deep=True),
        ProviderUsers(schema_version=config.provider.user_schema),
        {},
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "sftpwarden.yaml").write_text("source placeholder")
    (source / "users.yaml").write_text("source placeholder")
    entries = list(manifest.entries)
    if problem == "remote":
        entry.type = ContextType.REMOTE
    elif problem == "journal":
        (root / restore_existing.JOURNAL).parent.mkdir()
        (root / restore_existing.JOURNAL).write_text("invalid json")
    elif problem == "config-path":
        entry.config = str(root / "different.yaml")
    elif problem == "damaged-config":
        Path(entry.config).write_text("invalid: [")
    elif problem == "deployment":
        manifest.config.deploy.target = DeployTarget.KUBERNETES
    elif problem == "provider-type":
        manifest.config.provider.type = ProviderType.CSV
    elif problem == "provider-path":
        manifest.config.provider.path = "/etc/sftpwarden/other.yaml"
    elif problem in {"type-conflict", "child-conflict"}:
        (source / "data/nested").mkdir(parents=True)
        if problem == "type-conflict":
            (root / "data").write_text("existing file")
        else:
            (root / "data").mkdir()
            (root / "data/nested").write_text("existing child file")
        entries.append("data")
    else:
        monkeypatch.setattr(restore_existing.os, "access", lambda *a: False)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(RuntimeError):
        restore_existing.inspect_existing_target(entry, source, manifest, entries)
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_live_sqlite_sidecar_blocks_replacement(local_project_factory, tmp_path):
    root, entry = local_project_factory(provider=ProviderType.SQLITE)
    config = load_config(entry.config)
    provider = provider_from_config(root, config)
    users = ProviderUsers(schema_version=config.provider.user_schema)
    provider.write(users)
    provider.path.with_name(provider.path.name + "-shm").touch()
    manifest = ValidatedBackup([], "sftpwarden.yaml", config, users, {})
    with pytest.raises(RuntimeError, match="SQLite connections"):
        restore_existing.inspect_existing_target(entry, tmp_path, manifest, [])


def test_protected_destination_is_refused_before_any_restore_effect(
    local_project_factory, monkeypatch
):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    before = Path(entry.config).read_bytes()

    def denied(path):
        raise PermissionError("private runtime path")

    monkeypatch.setattr(restore_existing, "check_source_tree", denied)
    with pytest.raises(RuntimeError, match="operator's permissions") as failure:
        restore_backup(context_name=entry.name, backup_path=str(archive), maintenance=True)
    assert "private runtime path" not in str(failure.value)
    assert Path(entry.config).read_bytes() == before
    assert not (root / restore_existing.JOURNAL).exists()


@pytest.mark.parametrize("platform,uid", [("posix", 0), ("posix", 1001), ("nt", 0)])
def test_privileged_restore_retains_project_directory_owner(tmp_path, monkeypatch, platform, uid):
    monkeypatch.setattr(restore_existing, "os", SimpleNamespace(name=platform, geteuid=lambda: uid))
    expected = (tmp_path.stat().st_uid, tmp_path.stat().st_gid)
    assert restore_existing.project_owner(tmp_path) == (
        expected if platform == "posix" and uid == 0 else None
    )


def test_privileged_publication_preserves_operator_access(local_project_factory, monkeypatch):
    from sftpwarden.utils import persistence

    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    changes = []
    monkeypatch.setattr(restore_existing, "project_owner", lambda _: (1001, 1002))
    monkeypatch.setattr(
        persistence.os, "fchown", lambda fd, *owner: changes.append(owner), raising=False
    )
    monkeypatch.setattr(restore_existing.os, "chown", lambda *args, **kwargs: None, raising=False)
    restore_backup(context_name=entry.name, backup_path=str(archive))
    assert changes and set(changes) == {(1001, 1002)}
    assert load_config(entry.config)
    assert provider_from_config(root, load_config(entry.config)).read()
    journal = json.loads((root / restore_existing.JOURNAL).read_text())
    assert journal["phase"] == "files_restored"


@pytest.mark.parametrize(
    "problem", ["pending-config", "server", "mount", "compose", "identity-entries", "retained-uid"]
)
def test_runtime_restore_preflight_rejects_unapplied_or_mismatched_destinations(
    local_project_factory, tmp_path, problem
):
    from types import SimpleNamespace

    from sftpwarden.render.compose import write_compose
    from sftpwarden.runtime.core import RuntimeState, RuntimeUserState

    root, entry = local_project_factory()
    config = load_config(entry.config)
    write_compose(config, root, allow_local_build=False)
    manifest = ValidatedBackup(
        [],
        "sftpwarden.yaml",
        config.model_copy(deep=True),
        ProviderUsers(schema_version=config.provider.user_schema),
        {},
    )
    paths = {
        config.server.data_dir: root / "data",
        config.server.state_dir: root / "state",
        config.server.host_keys_dir: root / "host_keys",
        str(Path(config.provider.path).parent).replace("\\", "/"): root,
    }
    runtime = SimpleNamespace(
        config=config.model_copy(deep=True),
        info={
            "Mounts": [
                {"Destination": key, "Source": str(value), "Type": "bind"}
                for key, value in paths.items()
            ]
        },
    )
    entries = ["state", "host_keys"]
    source = tmp_path / "source"
    if problem == "pending-config":
        runtime.config.auth.allow_password = not config.auth.allow_password
    elif problem == "server":
        manifest.config.server.port += 1
    elif problem == "mount":
        runtime.info["Mounts"] = []
    elif problem == "compose":
        (root / config.docker.compose_file).write_text("services: {}")
    elif problem == "identity-entries":
        entries = ["state"]
    else:
        RuntimeState({"alice": RuntimeUserState(2001, 2001)}).save(root / "state/state.json")
        RuntimeState({"alice": RuntimeUserState(2002, 2002)}).save(source / "state/state.json")
    with pytest.raises(RuntimeError):
        restore_existing.inspect_runtime_destination(runtime, entry, manifest, source, entries)


def test_existing_dry_run_rejects_destination_link(local_project_factory):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    outside = root.parent / "unrelated.yaml"
    provider = root / "users.yaml"
    provider.rename(outside)
    provider.symlink_to(outside)
    with pytest.raises(RuntimeError, match="link"):
        restore_backup(context_name=entry.name, backup_path=str(archive), dry_run=True)
    assert provider.is_symlink()


def test_existing_dry_run_rejects_provider_destination_mismatch(local_project_factory):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    config = load_config(entry.config)
    config.provider.path = "/config/other.yaml"
    write_config(Path(entry.config), config)
    with pytest.raises(RuntimeError, match="destination"):
        restore_backup(context_name=entry.name, backup_path=str(archive), dry_run=True)


def test_existing_dry_run_inspects_current_provider(local_project_factory):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    (root / "users.yaml").unlink()
    with pytest.raises(RuntimeError, match="damaged"):
        restore_backup(context_name=entry.name, backup_path=str(archive), dry_run=True)


def test_existing_restore_reports_completed_file_steps(local_project_factory):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    unrelated = root / "notes.txt"
    unrelated.write_text("keep")
    result = restore_backup(context_name=entry.name, backup_path=str(archive))
    assert result.target_state == "existing"
    journal = json.loads((root / ".sftpwarden" / "restore-transaction.json").read_text())
    assert journal["phase"] == "files_restored"
    assert journal["pending"] == []
    assert set(journal["completed"]) == set(result.entries)
    assert result.safety_backup.is_file()
    assert unrelated.read_text() == "keep"


def test_failed_safety_verification_does_not_publish_files(local_project_factory, monkeypatch):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    config = load_config(entry.config)
    config.project.description = "current settings"
    write_config(Path(entry.config), config)
    before = Path(entry.config).read_bytes()

    def fail(_path):
        raise RuntimeError("Invalid safety archive")

    monkeypatch.setattr(restore_existing, "verify_safety_archive", fail)
    with pytest.raises(RuntimeError, match="Invalid safety"):
        restore_backup(context_name=entry.name, backup_path=str(archive))
    assert Path(entry.config).read_bytes() == before
    assert not (root / restore_existing.JOURNAL).exists()


def test_interrupted_file_publish_keeps_recovery_point_and_progress(
    local_project_factory,
    monkeypatch,
):
    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    provider_before = (root / "users.yaml").read_bytes()
    original = restore_existing.shutil.copyfileobj
    count = 0

    def fail_second(source, destination, *args, **kwargs):
        nonlocal count
        # Only destination publication uses atomic_binary_output here. Extraction
        # shares shutil, so select the isolated publication call below.
        count += 1
        if count == 2:
            destination.write(b"partial")
            raise OSError("simulated interruption")
        return original(source, destination, *args, **kwargs)

    publish = restore_existing.publish_existing_files

    def interrupt(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(restore_existing.shutil, "copyfileobj", fail_second)
            return publish(*args, **kwargs)

    monkeypatch.setattr(restore_existing, "publish_existing_files", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        restore_backup(context_name=entry.name, backup_path=str(archive))
    journal = json.loads((root / restore_existing.JOURNAL).read_text())
    assert journal["phase"] == "replacing_files"
    assert len(journal["completed"]) == 1
    assert journal["pending"]
    assert Path(journal["safety_backup"]).is_file()
    assert (root / "users.yaml").read_bytes() == provider_before
    with pytest.raises(RuntimeError, match="interrupted restore"):
        restore_backup(context_name=entry.name, backup_path=str(archive), dry_run=True)


def test_provider_edit_after_safety_capture_is_not_lost(local_project_factory, monkeypatch):
    from sftpwarden.providers import provider_from_config
    from sftpwarden.users import ProviderUsers, SFTPUser

    root, entry = local_project_factory()
    archive = root.parent / "source.tar.gz"
    create_backup(context_name=entry.name, output=str(archive))
    verify = restore_existing.verify_safety_archive
    provider = provider_from_config(root, load_config(entry.config))
    replacement = ProviderUsers(schema_version=2, users=[SFTPUser(username="later", disabled=True)])

    def concurrent_edit(path):
        result = verify(path)
        provider.write(replacement)
        return result

    monkeypatch.setattr(restore_existing, "verify_safety_archive", concurrent_edit)
    with pytest.raises(RuntimeError, match="Provider changed"):
        restore_backup(context_name=entry.name, backup_path=str(archive))
    assert provider.read() == replacement
    assert not (root / restore_existing.JOURNAL).exists()


def test_backup_default_names_cannot_overwrite_another_safety_point():
    from sftpwarden.services.backup import default_backup_path

    assert len({default_backup_path("pre-restore") for _ in range(20)}) == 20


@pytest.mark.parametrize("failure", [None, "capture", "verify", "publish"])
@pytest.mark.parametrize("running", [True, False])
def test_runtime_restore_resumes_only_before_any_replacement(
    local_project_factory,
    monkeypatch,
    failure,
    running,
):
    from contextlib import contextmanager

    from sftpwarden.config import provider_local_path
    from sftpwarden.render.compose import write_compose
    from sftpwarden.runtime.core import RuntimeState
    from sftpwarden.runtime.recovery import BLOCK_FILE
    from sftpwarden.services import backup_runtime
    from sftpwarden.services.backup import write_backup_archive
    from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR

    root, entry = local_project_factory()
    config = load_config(entry.config)
    config.provider.path = f"{CONTAINER_PROVIDER_DATA_DIR}/users.yaml"
    (root / "provider").mkdir()
    (root / "users.yaml").rename(root / "provider" / "users.yaml")
    write_config(Path(entry.config), config)
    write_compose(config, root, allow_local_build=False)
    RuntimeState(users={}).save(root / "state" / "state.json")
    (root / "host_keys").mkdir()
    (root / "host_keys" / "synthetic-key").write_text("synthetic fixture")
    archive = root.parent / "runtime.tar.gz"
    write_backup_archive(
        output_path=archive,
        root=root,
        entries=[
            "sftpwarden.yaml",
            "docker-compose.yml",
            "provider/users.yaml",
            "state",
            "host_keys",
        ],
        context_name=entry.name,
        provider_type="yaml",
        include_data=False,
        ownership=lambda *_args: (0, 0),
    )
    paths = {
        config.server.data_dir: root / "data",
        config.server.state_dir: root / "state",
        config.server.host_keys_dir: root / "host_keys",
        CONTAINER_PROVIDER_DATA_DIR: provider_local_path(root, config).parent,
    }
    runtime = backup_runtime.RuntimeCapture(
        "owned",
        root / "applied.json",
        config,
        {
            "State": {"Running": running},
            "Mounts": [
                {"Destination": target, "Source": str(host), "Type": "bind"}
                for target, host in paths.items()
            ],
        },
    )
    effects = []

    @contextmanager
    def inspect(_entry):
        yield runtime

    def capture(_runtime, stream, **kwargs):
        effects.append("capture")
        if failure == "capture":
            raise RuntimeError("capture failed")
        stream.write(archive.read_bytes())

    verify = restore_existing.verify_safety_archive

    def verify_capture(path):
        if failure == "verify":
            raise RuntimeError("verification failed")
        return verify(path)

    publish = restore_existing.publish_existing_files

    def fail_copy(*_args, **_kwargs):
        raise OSError("interrupted file copy")

    def publish_capture(*args, **kwargs):
        with monkeypatch.context() as patch:
            if failure == "publish":
                patch.setattr(restore_existing.shutil, "copyfileobj", fail_copy)
            return publish(*args, **kwargs)

    monkeypatch.setattr(restore_existing, "inspect_runtime", inspect)
    monkeypatch.setattr(restore_existing, "capture_runtime", capture)
    monkeypatch.setattr(restore_existing, "verify_safety_archive", verify_capture)
    monkeypatch.setattr(restore_existing, "publish_existing_files", publish_capture)
    monkeypatch.setattr(backup_runtime, "_run", lambda *args, **kwargs: effects.append("stop"))
    monkeypatch.setattr(backup_runtime, "_resume", lambda *_args: effects.append("resume"))
    if failure:
        with pytest.raises(RuntimeError):
            restore_backup(context_name=entry.name, backup_path=str(archive), maintenance=True)
    else:
        restore_backup(context_name=entry.name, backup_path=str(archive), maintenance=True)
    assert ("stop" in effects) is running
    assert ("resume" in effects) is (running and failure in {"capture", "verify"})
    assert (root / "state" / BLOCK_FILE).exists() is (failure == "publish")
    if failure in {"capture", "verify"}:
        assert not (root / restore_existing.JOURNAL).exists()
