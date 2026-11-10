"""Recovery dispatch keeps selected contexts, maintenance and database protection explicit."""

import io
import tarfile
from pathlib import Path

import pytest

from sftpwarden.config import DeployTarget, ProviderType, load_config, write_config
from sftpwarden.contexts import ContextType, RemoteEndpoint
from sftpwarden.services import (
    backup,
    backup_kubernetes,
    backup_runtime,
    restore_external,
    restore_kubernetes,
    restore_remote,
)
from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("target", ["cluster", "runtime"])
def test_backup_dispatch_preserves_maintenance_and_requested_data(
    local_project_factory, monkeypatch, target
):
    root, entry = local_project_factory()
    config = load_config(entry.config)
    if target == "cluster":
        config.deploy.target = DeployTarget.KUBERNETES
        write_config(entry.config, config)
    calls = []

    def capture(selected, output, **kwargs):
        calls.append((selected, kwargs))
        return backup.BackupResult(path=output, entries=[])

    monkeypatch.setattr(
        backup_kubernetes if target == "cluster" else backup_runtime,
        "create_kubernetes_backup" if target == "cluster" else "create_runtime_backup",
        capture,
    )
    output = root.parent / "point.tgz"
    assert (
        backup.create_backup(
            context_name=entry.name,
            output=str(output),
            maintenance=True,
            include_data=True,
            dry_run=True,
        ).path
        == output
    )
    assert calls[0][1]["include_data"] and calls[0][1]["dry_run"]
    assert not output.exists()


@pytest.mark.parametrize(
    "options",
    [
        {"target_root": "new", "context_name": "selected"},
        {"provider_context": "protected"},
        {"target_root": "new", "maintenance": True},
    ],
)
def test_ambiguous_recovery_selection_is_rejected_before_opening_archive(options):
    options = {"context_name": None, **options}
    with pytest.raises(RuntimeError):
        backup.restore_backup(backup_path="never-opened.tgz", **options)


@pytest.mark.parametrize(
    "route",
    [
        "remote-file",
        "remote-external",
        "remote-mismatch",
        "remote-unprotected",
        "remote-extra-provider",
        "local-extra-provider",
        "cluster",
        "no-root",
        "fresh-external",
    ],
)
def test_restore_routes_only_verified_input_to_selected_destination(
    local_project_factory, tmp_path, monkeypatch, route
):
    root, entry = local_project_factory()
    archive = tmp_path / "point.tgz"
    backup.create_backup(context_name=entry.name, output=str(archive))
    source = tmp_path / "source"
    with tarfile.open(archive) as packed:
        headers = backup.safe_extract(packed, source, private_staging=True)
    manifest = validate_backup_manifest(source, headers)
    protected = entry.model_copy(deep=True)
    calls = []
    options = {"context_name": None if route == "fresh-external" else entry.name}
    if route.startswith("remote"):
        entry.type = ContextType.REMOTE
        entry.remote = RemoteEndpoint(
            host="example.invalid",
            user="operator",
            remote_root="/srv/recovered",
            remote_config="/srv/recovered/sftpwarden.yaml",
        )
    if route in {"remote-external", "remote-unprotected", "fresh-external"}:
        manifest.config.provider.type = ProviderType.POSTGRESQL
        manifest.config.provider.dsn = "postgresql://db/synthetic"
    if route == "remote-mismatch":
        entry.remote.compose_file = "different.yaml"
    if route in {
        "remote-external",
        "remote-extra-provider",
        "local-extra-provider",
        "fresh-external",
    }:
        options["provider_context"] = "protected"
    if route == "cluster":
        config = load_config(entry.config)
        config.deploy.target = DeployTarget.KUBERNETES
        write_config(entry.config, config)
    if route == "no-root":
        entry.root = ""
    if route == "fresh-external":
        options["target_root"] = str(tmp_path / "fresh")
    monkeypatch.setattr(backup, "validate_backup_manifest", lambda *a: manifest)
    monkeypatch.setattr(
        backup,
        "resolve_context",
        lambda **kwargs: protected if kwargs.get("context_name") == "protected" else entry,
    )
    monkeypatch.setattr(
        restore_external, "protected_provider", lambda selected, valid: manifest.config.provider
    )

    def rebind(source, selected, provider, destination):
        assert selected is manifest and provider == manifest.config.provider
        destination.write_bytes(b"rebound stream")
        calls.append("rebind")

    monkeypatch.setattr(restore_external, "rebind_archive", rebind)
    monkeypatch.setattr(
        restore_remote,
        "restore_fresh_remote",
        lambda selected, packed, **kwargs: calls.append((selected, packed.name, kwargs)),
    )
    monkeypatch.setattr(
        restore_kubernetes,
        "restore_new_cluster",
        lambda *args, **kwargs: calls.append((args[0], kwargs)),
    )
    monkeypatch.setattr(
        restore_external,
        "restore_external_fresh",
        lambda *args, **kwargs: (Path(options["target_root"]), tmp_path / "safety.tgz"),
    )
    if route in {
        "remote-mismatch",
        "remote-unprotected",
        "remote-extra-provider",
        "local-extra-provider",
        "no-root",
    }:
        with pytest.raises(RuntimeError):
            backup.restore_backup(backup_path=str(archive), dry_run=True, **options)
        assert not calls
    else:
        result = backup.restore_backup(
            backup_path=str(archive), dry_run=True, include_data=True, **options
        )
        assert result.path == archive
        if route == "remote-external":
            assert calls[0] == "rebind"
            assert calls[1][1] == "protected-recovery.tgz"
            assert calls[1][2]["protected_external"] is True
        elif route == "fresh-external":
            assert result.safety_backup == tmp_path / "safety.tgz"
        else:
            assert calls[0][0] is entry


@pytest.mark.parametrize(
    "problem", ["no-output", "two-outputs", "fingerprint", "metadata-collision", "inside-source"]
)
def test_capture_rejects_ambiguous_output_or_unapplied_policy(
    local_project_factory, tmp_path, problem
):
    root, entry = local_project_factory()
    config = load_config(entry.config)
    output = tmp_path / "point.tgz"
    options = {"output_path": output}
    entries = ["sftpwarden.yaml", "users.yaml"]
    if problem == "no-output":
        options["output_path"] = None
    elif problem == "two-outputs":
        options["stream"] = io.BytesIO()
    elif problem == "fingerprint":
        options["applied_fingerprint"] = "unapplied"
    elif problem == "metadata-collision":
        entries.append("manifest.json")
    else:
        options["output_path"] = root / "users.yaml"
    before = (root / "users.yaml").read_bytes()
    with pytest.raises(RuntimeError):
        backup.write_backup_archive(
            root=root,
            entries=entries,
            context_name=entry.name,
            provider_type=config.provider.type.value,
            include_data=False,
            **options,
        )
    assert (root / "users.yaml").read_bytes() == before
    assert not output.exists()
