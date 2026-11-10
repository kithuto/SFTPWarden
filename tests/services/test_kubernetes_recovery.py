"""Recovery ownership, replica inspection and narrow volume selection."""

from __future__ import annotations

import json
import subprocess
import tarfile
from contextlib import nullcontext
from copy import deepcopy
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest

from sftpwarden.config import default_project_config, load_config
from sftpwarden.render.kubernetes import kubernetes_manifests
from sftpwarden.services import backup_kubernetes, kubernetes_lock
from sftpwarden.services.backup import create_backup
from sftpwarden.services.backup_kubernetes import capture_specification, create_kubernetes_backup
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize(
    "outcome", ["dry-run", "policy", "users", "identity", "occupied", "secret", "stream", "helm"]
)
def test_cluster_restore_preflight_and_failed_stream_preserve_claims(
    local_project_factory, monkeypatch, outcome
):
    from sftpwarden.config import KubernetesMode
    from sftpwarden.providers import provider_from_config
    from sftpwarden.render.kubernetes import kubernetes_runtime_config
    from sftpwarden.services import restore_kubernetes

    root, entry = local_project_factory()
    config = load_config(entry.config)
    config.kubernetes.mode = KubernetesMode.HELM if outcome == "helm" else KubernetesMode.MANIFESTS
    manifest = SimpleNamespace(
        config=kubernetes_runtime_config(config),
        users=provider_from_config(root, config).read(),
        entries={"host_keys", "state"},
    )
    if outcome == "policy":
        manifest.config.server.port += 1
    elif outcome == "users":
        manifest.users = manifest.users.model_copy(update={"schema_version": 1})
    elif outcome == "identity":
        manifest.entries = {"state"}
    elif outcome == "secret":
        config.kubernetes.host_keys_secret = "operator"
    archive = root / "input.tgz"
    archive.write_bytes(b"verified stream boundary")
    created, commands = [], []
    monkeypatch.setattr(restore_kubernetes, "pin_context", lambda cfg: cfg)
    monkeypatch.setattr(restore_kubernetes, "operation_lock", lambda *a: nullcontext())

    def resource(cfg, kind, name):
        if outcome == "occupied" and kind == "statefulset":
            return {"metadata": {"name": name}}
        return next(
            (
                item
                for item in created
                if kind == "pod" and item["kind"] == "Pod" and item["metadata"]["name"] == name
            ),
            None,
        )

    monkeypatch.setattr(restore_kubernetes, "get_resource", resource)
    monkeypatch.setattr(
        restore_kubernetes, "write_resource", lambda cfg, doc, **kw: created.append(doc)
    )
    monkeypatch.setattr(restore_kubernetes, "request", lambda cfg, args: commands.append(args))
    monkeypatch.setattr(restore_kubernetes, "read_keys", lambda path: {})
    monkeypatch.setattr(
        restore_kubernetes.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, int(outcome == "stream")),
    )
    if outcome in {"dry-run", "helm"}:
        restore_kubernetes.restore_new_cluster(
            entry, config, archive, root, manifest, include_data=True, dry_run=outcome == "dry-run"
        )
        if outcome == "dry-run":
            assert not created and not commands
        else:
            claims = [doc for doc in created if doc["kind"] == "PersistentVolumeClaim"]
            assert claims and all(
                doc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
                for doc in claims
            )
    else:
        with pytest.raises(RuntimeError):
            restore_kubernetes.restore_new_cluster(
                entry, config, archive, root, manifest, include_data=True, dry_run=False
            )
        if outcome != "stream":
            assert not created
    assert not any("delete" in args and "pvc" in args for args in commands)
    if outcome in {"stream", "helm"}:
        assert commands[-1][:2] == ["delete", "pod"]


def test_restore_helper_uses_linux_mount_paths_on_windows(local_project_factory, monkeypatch):
    from sftpwarden.providers import provider_from_config
    from sftpwarden.render.kubernetes import kubernetes_runtime_config
    from sftpwarden.services import restore_kubernetes

    root, entry = local_project_factory()
    config = load_config(entry.config)
    manifest = SimpleNamespace(
        config=kubernetes_runtime_config(config),
        users=provider_from_config(root, config).read(),
        entries={"host_keys", "state"},
    )
    archive = root / "input.tgz"
    archive.write_bytes(b"stream supplied to the container")
    created = []
    monkeypatch.setattr(restore_kubernetes, "Path", PureWindowsPath)
    monkeypatch.setattr(restore_kubernetes, "pin_context", lambda cfg: cfg)
    monkeypatch.setattr(restore_kubernetes, "operation_lock", lambda *a: nullcontext())
    monkeypatch.setattr(restore_kubernetes, "get_resource", lambda *a: None)
    monkeypatch.setattr(
        restore_kubernetes, "write_resource", lambda cfg, resource, **kw: created.append(resource)
    )
    monkeypatch.setattr(restore_kubernetes, "request", lambda *a: b"")
    monkeypatch.setattr(
        restore_kubernetes.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0),
    )
    restore_kubernetes.restore_new_cluster(
        entry, config, archive, root, manifest, include_data=False, dry_run=False
    )
    pod = next(resource for resource in created if resource["kind"] == "Pod")
    mounts = pod["spec"]["containers"][0]["volumeMounts"]
    provider_mount = next(mount for mount in mounts if mount["name"] == "provider")
    assert provider_mount["mountPath"] == "/etc/sftpwarden/provider-data"
    assert all("\\" not in mount["mountPath"] for mount in mounts)


def test_operation_lock_keeps_other_owner_and_does_not_enter(monkeypatch):
    config = default_project_config("test")
    monkeypatch.setattr(
        kubernetes_lock,
        "write_resource",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("already held")),
    )
    monkeypatch.setattr(
        kubernetes_lock,
        "get_resource",
        lambda *a: {"metadata": {"labels": {"sftpwarden.io/operation-owner": "other"}}},
    )
    monkeypatch.setattr(
        kubernetes_lock, "request", lambda *a: pytest.fail("deleted another operation")
    )
    with (
        pytest.raises(RuntimeError, match="already held"),
        kubernetes_lock.operation_lock(config, "test"),
    ):
        pytest.fail("entered locked operation")


@pytest.mark.parametrize("create_uncertain", [False, True])
def test_operation_lock_cleans_own_uncertain_create(monkeypatch, create_uncertain):
    stored = []
    removed = []

    def create(_config, document, **kwargs):
        stored.append(document)
        if create_uncertain:
            raise RuntimeError("lost create reply")

    monkeypatch.setattr(kubernetes_lock, "write_resource", create)
    monkeypatch.setattr(kubernetes_lock, "get_resource", lambda *a: stored[0])
    monkeypatch.setattr(kubernetes_lock, "request", lambda _cfg, command: removed.append(command))
    with (
        pytest.raises(RuntimeError, match="lost create reply|operation failed"),
        kubernetes_lock.operation_lock(default_project_config("test"), "capture"),
    ):
        raise RuntimeError("operation failed")
    assert removed[0][:2] == ["delete", "configmap"]


def test_capture_helper_limits_mounts_and_requires_persistent_provider():
    config = default_project_config("test")
    mounts = [
        {"name": name, "mountPath": path}
        for name, path in [
            ("data", "/data"),
            ("state", "/state"),
            ("provider", "/etc/sftpwarden/provider-data"),
            ("unrelated", "/operator-private"),
        ]
    ]
    volumes = [
        {"name": name, "persistentVolumeClaim": {"claimName": "test-" + name}}
        for name in ["data", "state", "provider", "unrelated"]
    ]
    workload = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "sftpwarden", "volumeMounts": mounts}],
                    "volumes": volumes,
                }
            }
        }
    }
    pod = {"spec": {"nodeName": "selected-node"}}
    spec = capture_specification(workload, pod, config)
    assert not spec["automountServiceAccountToken"]
    selected = spec["containers"][0]["volumeMounts"]
    assert {item["name"] for item in selected} == {"data", "state", "provider", "captured-keys"}
    assert all(item["readOnly"] for item in selected if item["name"] != "captured-keys")
    invalid = deepcopy(workload)
    invalid["spec"]["template"]["spec"]["volumes"][2] = {"name": "provider", "emptyDir": {}}
    with pytest.raises(RuntimeError, match="persistent"):
        capture_specification(invalid, pod, config)


def test_cluster_capture_requires_maintenance_even_with_local_project(tmp_path):
    with pytest.raises(RuntimeError, match="maintenance"):
        create_kubernetes_backup(
            default_project_config("test"),
            tmp_path / "backup.tgz",
            include_data=False,
            maintenance=False,
            dry_run=True,
        )
    result = create_kubernetes_backup(
        default_project_config("test"),
        tmp_path / "backup.tgz",
        include_data=True,
        maintenance=True,
        dry_run=True,
    )
    assert "cluster:data" in result.entries
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("workload", [None, {"spec": {"replicas": 0}}])
def test_absent_or_stopped_cluster_is_not_an_empty_recovery_source(tmp_path, monkeypatch, workload):
    monkeypatch.setattr(backup_kubernetes, "pin_context", lambda cfg: cfg)
    monkeypatch.setattr(backup_kubernetes, "operation_lock", lambda *a: nullcontext())
    monkeypatch.setattr(backup_kubernetes, "get_resource", lambda *a: workload)
    monkeypatch.setattr(backup_kubernetes, "request", lambda *a: pytest.fail("mutated source"))
    output = tmp_path / "archive.tgz"
    with pytest.raises(RuntimeError, match="running single-replica"):
        create_kubernetes_backup(
            default_project_config("test"),
            output,
            include_data=True,
            maintenance=True,
            dry_run=False,
        )
    assert not output.exists()


@pytest.mark.parametrize("failure", [None, "capture", "corrupt", "cleanup", "resume", "identity"])
def test_cluster_capture_publishes_only_after_verified_resumption(
    local_project_factory,
    monkeypatch,
    failure,
):
    root, entry = local_project_factory()
    config = load_config(entry.config)
    valid_archive = root.parent / "input.tgz"
    create_backup(context_name=entry.name, output=str(valid_archive))
    content = valid_archive.read_bytes()
    output = root.parent / "output.tgz"
    output.write_bytes(b"previous recovery point")
    workload = next(
        item for item in kubernetes_manifests(config, root) if item["kind"] == "StatefulSet"
    )
    workload["metadata"].update(uid="workload", resourceVersion="1")
    name = workload["metadata"]["name"]
    pod = {
        "metadata": {
            "name": name + "-0",
            "uid": "original",
            "ownerReferences": [{"uid": "workload"}],
        },
        "spec": {"nodeName": "node"},
    }
    resources = {("statefulset", name): workload, ("pod", name + "-0"): pod}
    events = []
    monkeypatch.setattr(backup_kubernetes, "pin_context", lambda cfg: cfg)
    monkeypatch.setattr(backup_kubernetes, "operation_lock", lambda *a: nullcontext())
    monkeypatch.setattr(
        backup_kubernetes,
        "get_resource",
        lambda cfg, kind, key: deepcopy(resources.get((kind, key))),
    )

    def capture(cfg, workload, selected):
        return {}, {
            "ed25519": "changed"
            if failure == "identity" and selected["metadata"]["uid"] == "resumed"
            else "trusted"
        }

    monkeypatch.setattr(backup_kubernetes, "_capture", capture)

    def request(cfg, args, payload=None):
        events.append(args)
        if args[0] == "exec" and "-i" not in args:
            return json.dumps(
                {
                    "applied": {"format": 1, "config": config.model_dump(mode="json")},
                    "environment": {},
                }
            ).encode()
        if args[0] == "scale":
            if "--replicas=1" in args:
                if failure == "resume":
                    raise RuntimeError("injected resume failure")
                workload["spec"]["replicas"] = 1
                pod["metadata"]["uid"] = "resumed"
            else:
                workload["spec"]["replicas"] = 0
        if args[0] == "delete":
            if failure == "cleanup":
                raise RuntimeError("injected cleanup failure")
            resources.pop(("pod", args[2]))
        return b""

    def create(cfg, document, **kwargs):
        assert workload["spec"]["replicas"] == 0
        assert output.read_bytes() == b"previous recovery point"
        resources[("pod", document["metadata"]["name"])] = document

    def stream(command, **kwargs):
        assert workload["spec"]["replicas"] == 0
        assert kwargs["stderr"] == subprocess.DEVNULL
        kwargs["stdout"].write(b"corrupt archive" if failure == "corrupt" else content)
        return subprocess.CompletedProcess(command, int(failure == "capture"))

    monkeypatch.setattr(backup_kubernetes, "request", request)
    monkeypatch.setattr(backup_kubernetes, "write_resource", create)
    monkeypatch.setattr(backup_kubernetes.subprocess, "run", stream)
    if failure:
        with pytest.raises((RuntimeError, tarfile.TarError)):
            create_kubernetes_backup(
                config, output, include_data=False, maintenance=True, dry_run=False
            )
        assert output.read_bytes() == b"previous recovery point"
    else:
        result = create_kubernetes_backup(
            config, output, include_data=False, maintenance=True, dry_run=False
        )
        assert result.path == output
        assert output.read_bytes() == content
    assert workload["spec"]["replicas"] == (0 if failure == "resume" else 1)
    assert any(command[:2] == ["delete", "pod"] for command in events)
    assert not any(command[:2] == ["delete", "pvc"] for command in events)
