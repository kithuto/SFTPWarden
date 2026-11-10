"""Consent, artifact preparation and truthful recovery boundaries for updates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import load_config, write_config
from sftpwarden.contexts import ContextRegistry, save_registry
from sftpwarden.services import deployment_upgrade as deploy
from sftpwarden.services import (
    installation,
    update_artifacts,
    update_compatibility,
    upgrade,
    upgrade_worker,
)
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.watcher import upgrade as watcher


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "private"))


def test_service_cannot_bypass_all_context_consent(monkeypatch):
    monkeypatch.setattr(watcher, "pause_watcher", lambda: pytest.fail("watcher stopped"))
    plan = {"cli": {"previous": get_version()}, "deployments": {"requires_deploy_all": True}}
    with pytest.raises(SFTPWardenError, match="consent"):
        upgrade.apply_management_update(plan, json_output=True)
    assert not upgrade.receipt_path().exists()


@pytest.mark.parametrize("change", ["registry", "target", "config"])
def test_changed_consent_snapshot_prevents_all_mutations(local_project_factory, change):
    _, entry = local_project_factory()
    registry = ContextRegistry(contexts={entry.name: entry})
    save_registry(registry)
    contexts = [{"name": entry.name, "identity": update_compatibility.context_identity(entry)}]
    if change == "registry":
        registry.contexts.clear()
    elif change == "target":
        registry.contexts[entry.name].root += "-different"
    else:
        config = load_config(entry.config)
        config.docker.image = "custom.invalid/runtime:changed"
        write_config(entry.config, config)
    save_registry(registry)
    plan = {"cli": {"previous": get_version()}, "deployments": {"contexts": contexts}}
    with pytest.raises(SFTPWardenError, match="changed"):
        upgrade.apply_management_update(plan, json_output=True)
    assert not upgrade.receipt_path().exists()


def test_bad_artifact_does_not_write_migration_or_config(local_project_factory, monkeypatch):
    _, entry = local_project_factory()
    before = Path(entry.config).read_bytes()
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: "1.3.0")
    plan = deploy.plan_upgrade(entry)
    monkeypatch.setattr(
        update_artifacts,
        "preflight_runtime",
        lambda *a: (_ for _ in ()).throw(SFTPWardenError("artifact unavailable")),
    )
    with pytest.raises(SFTPWardenError, match="artifact"):
        deploy.begin_upgrade(entry, plan)
    assert not deploy.receipt_path(entry).exists()
    assert Path(entry.config).read_bytes() == before


@pytest.mark.parametrize("mode", ["manifests", "helm"])
def test_explicit_cluster_command_inspects_and_verifies_cluster_not_compose(
    local_project_factory, monkeypatch, mode
):
    from sftpwarden.config import DeployTarget, KubernetesMode

    _, entry = local_project_factory()
    original = Path(entry.config).read_bytes()
    observed = []

    def probe(entry, *, config):
        observed.append((config.deploy.target, config.kubernetes.mode))
        return get_version()

    monkeypatch.setattr(deploy, "deployed_version", probe)
    monkeypatch.setattr(watcher, "upgrade_watcher", lambda: {"status": "not_installed"})
    plan = deploy.plan_upgrade(entry, kubernetes_mode=KubernetesMode(mode))
    assert Path(entry.config).read_bytes() == original
    deploy.begin_upgrade(entry, plan)
    deploy.complete_upgrade(entry, plan)
    assert observed == [(DeployTarget.KUBERNETES, KubernetesMode(mode))] * 2
    receipt = deploy.receipt_path(
        entry, kube_context=plan.kube_context, kubernetes_mode=plan.kubernetes_mode
    )
    assert json.loads(receipt.read_text())["status"] == "complete"


def test_stopped_runtime_inspection_uses_immutable_image_without_mounts(
    local_project_factory, monkeypatch
):
    _, entry = local_project_factory()
    image = "sha256:" + "a" * 64
    commands = []

    def request(command, **kwargs):
        commands.append(command)
        if "ps" in command:
            return "existing-container"
        if "exec" in command:
            raise deploy.RuntimeError("stopped")
        if "inspect" in command:
            return json.dumps([{"State": {"Running": False}, "Image": image}])
        if "ls" in command:
            return ""
        assert "--rm" in command and "none" in command and "--read-only" in command
        assert image in command and not {"-v", "--mount", "start"}.intersection(command)
        return "1.3.0"

    monkeypatch.setattr(deploy, "request", request)
    assert deploy.deployed_version(entry) == "1.3.0"
    assert len(commands) == 5


def test_running_unreachable_runtime_is_not_started_as_a_probe(local_project_factory, monkeypatch):
    _, entry = local_project_factory()

    def request(command, **kwargs):
        if "ps" in command:
            return "container"
        if "exec" in command:
            raise deploy.RuntimeError("access denied")
        assert "inspect" in command
        return json.dumps([{"State": {"Running": True}}])

    monkeypatch.setattr(deploy, "request", request)
    with pytest.raises(SFTPWardenError, match="access denied"):
        deploy.deployed_version(entry)


@pytest.mark.parametrize("status", ["Succeeded", "Failed", "missing"])
def test_kubernetes_candidate_cleanup_is_registered_before_creation(monkeypatch, status):
    from sftpwarden.config import SFTPWardenConfig
    from sftpwarden.services import kubernetes_identity as kube

    state = {}
    calls = []

    def create(config, pod, **kwargs):
        state.update(pod)
        if status == "missing":
            raise SFTPWardenError("uncertain create")

    monkeypatch.setattr(kube, "write_resource", create)
    monkeypatch.setattr(kube, "get_resource", lambda *a: {**state, "status": {"phase": status}})
    monkeypatch.setattr(kube, "request", lambda c, args: calls.append(args) or b"1.3.1\n")
    config = SFTPWardenConfig.model_validate({"project": {"name": "test"}})
    if status == "Succeeded":
        assert update_artifacts.kubernetes_candidate(config, "candidate") == "1.3.1"
    else:
        with pytest.raises(SFTPWardenError):
            update_artifacts.kubernetes_candidate(config, "candidate")
    assert calls[-1][:2] == ["delete", "pod"]
    assert not state["spec"].get("volumes")
    assert state["spec"]["automountServiceAccountToken"] is False


def test_preparation_failure_keeps_watcher_running_and_preserves_plan(tmp_path, monkeypatch):
    path = tmp_path / "operation.json"
    plan = {"cli": {"target": "1.3.1"}, "installation": {"manager": "pip"}}
    path.write_text(json.dumps({"plan": plan}))
    monkeypatch.setattr(watcher, "pause_watcher", lambda: pytest.fail("stopped before preparation"))
    monkeypatch.setattr(
        installation,
        "prepare_installation",
        lambda *a: (_ for _ in ()).throw(SFTPWardenError("download failed private-token")),
    )
    monkeypatch.setattr(upgrade_worker.sys, "argv", ["worker", str(path)])
    assert upgrade_worker.main() == 1
    record = json.loads(path.read_text())
    assert record["status"] == "incomplete" and record["plan"] == plan
    assert "private-token" not in path.read_text()


def test_completed_watcher_receipt_needs_current_process_evidence(monkeypatch):
    from sftpwarden.config.global_config import load_global_config, save_global_config
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode="systemd", activate=False)
    watcher.upgrade_watcher()
    state = load_global_config()
    state.watcher.activated = True
    save_global_config(state)
    assert watcher.watcher_upgrade_plan()["changed"] is True
    receipt = watcher.read_receipt()
    receipt["process"] = {"instance": "live"}
    watcher.installation_receipt().write_text(json.dumps(receipt))
    process = {"instance": "live", "version": get_version(), "heartbeat": 100}
    watcher.process_path().parent.mkdir(parents=True, exist_ok=True)
    watcher.process_path().write_text(json.dumps(process))
    monkeypatch.setattr(watcher.time, "time", lambda: 101)
    assert watcher.watcher_upgrade_plan()["changed"] is False
    monkeypatch.setattr(watcher.time, "time", lambda: 111)
    assert watcher.watcher_upgrade_plan()["changed"] is True


def test_direct_watcher_update_refuses_incompatible_contexts_before_stop(monkeypatch):
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode="systemd", activate=False)
    monkeypatch.setattr(
        update_compatibility, "assess_contexts", lambda *a: {"requires_deploy_all": True}
    )
    monkeypatch.setattr(watcher, "_execute", lambda _: pytest.fail("watcher touched"))
    with pytest.raises(SFTPWardenError, match="incompatible"):
        watcher.upgrade_watcher()
    assert not watcher.installation_receipt().exists()


def test_manager_identity_and_provider_requirement(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installation.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=str(tmp_path)),
    )
    installation.manager_directory(["manager", "dir"], tmp_path)
    with pytest.raises(SFTPWardenError, match="does not own"):
        installation.manager_directory(["manager", "dir"], tmp_path / "other")
    requirement = installation.release_requirement(
        "1.3.1", ["PyMySQL", "psycopg-binary", "pymongo"]
    )
    assert requirement == "sftpwarden[mysql,postgres,mongodb]==1.3.1"


def test_deployment_settings_changed_after_confirmation_are_rejected(
    local_project_factory, monkeypatch
):
    _, entry = local_project_factory()
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: "1.3.0")
    plan = deploy.plan_upgrade(entry)
    config = load_config(entry.config)
    config.server.port += 1
    write_config(entry.config, config)
    monkeypatch.setattr(
        update_artifacts, "preflight_runtime", lambda *a: pytest.fail("candidate changed")
    )
    with pytest.raises(SFTPWardenError, match="changed after confirmation"):
        deploy.begin_upgrade(entry, plan)
    assert not deploy.receipt_path(entry).exists()


def test_probe_timeout_removes_only_owned_container_and_anonymous_volumes():
    from sftpwarden.services.image_probe import probe_version

    calls = []

    def execute(command):
        calls.append(command)
        if "run" in command:
            raise TimeoutError("client timed out")
        if "ls" in command:
            return "abcdef123456"
        return ""

    with pytest.raises(TimeoutError):
        probe_version(execute, "candidate")
    assert calls[-1] == ["docker", "rm", "-f", "-v", "abcdef123456"]
    assert calls[1][-1].startswith("label=sftpwarden.io/version-probe=")
    assert not any("prune" in command for command in calls)


def test_watcher_stop_retry_retains_confirmed_progress_and_original_backup(monkeypatch):
    from sftpwarden.config.global_config import load_global_config, save_global_config
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode="systemd", activate=False)
    state = load_global_config()
    state.watcher.activated = True
    save_global_config(state)
    backend = watcher.watcher_class(watcher.WatcherInstallMode.SYSTEMD)
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    monkeypatch.setattr(backend, "uninstall_commands", lambda **kw: [["stop"], ["remove"]])
    calls = []

    def execute(commands):
        calls.extend(commands)
        if commands == [["remove"]] and len(calls) == 2:
            raise SFTPWardenError("injected removal failure")

    monkeypatch.setattr(watcher, "_execute", execute)
    before = Path(state.watcher.path).read_bytes()
    with pytest.raises(SFTPWardenError, match="removal"):
        watcher.pause_watcher()
    assert watcher.read_receipt()["stop_step"] == 1
    watcher.pause_watcher()
    assert calls == [["stop"], ["remove"], ["remove"]]
    assert watcher.read_receipt()["status"] == "paused"
    assert watcher.installation_receipt().with_name("previous-installation").read_bytes() == before


def test_status_reports_partial_failure_without_network(monkeypatch):
    from typer.testing import CliRunner

    from sftpwarden.cli import app

    path = upgrade.receipt_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"status": "incomplete", "stage": "verifying_components", "action": "retry"})
    )
    monkeypatch.setattr(
        upgrade, "discover_release", lambda **kw: pytest.fail("network used for status")
    )
    result = CliRunner().invoke(app, ["upgrade", "--status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["stage"] == "verifying_components"


@pytest.mark.parametrize("contents", ['{"status":"running"}', '{"private":"secret"}', "invalid"])
def test_status_handles_running_and_damaged_receipts(contents):
    from typer.testing import CliRunner

    from sftpwarden.cli import app

    path = upgrade.receipt_path()
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    result = CliRunner().invoke(app, ["upgrade", "--status"])
    assert result.exit_code == (0 if "running" in contents else 1)
    assert "secret" not in result.output
    assert "running" in result.output if "running" in contents else "receipt" in result.output


@pytest.mark.parametrize(
    "mode", ["systemd", "openrc", "runit", "supervisord", "launchd", "windows-task"]
)
def test_native_replacement_stops_old_service_before_starting_and_verifying(mode, monkeypatch):
    from sftpwarden.config.global_config import load_global_config, save_global_config
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode=mode, activate=False)
    state = load_global_config()
    state.watcher.activated = True
    save_global_config(state)
    backend = watcher.watcher_class(watcher.WatcherInstallMode(mode))
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    calls = []
    monkeypatch.setattr(watcher, "_execute", lambda commands: calls.extend(commands))

    def verified(*args):
        calls.append(["verified"])
        return {"instance": "replacement", "version": get_version()}

    monkeypatch.setattr(watcher, "verify_process", verified)
    expected_stop = backend.uninstall_commands(path=Path(state.watcher.path))
    if mode == "windows-task":
        expected_stop.insert(0, ["schtasks", "/End", "/TN", "SFTPWarden Watcher"])
    result = watcher.upgrade_watcher()
    assert calls == [*expected_stop, *backend.commands(), ["verified"]]
    assert result["process"]["instance"] == "replacement"
    assert watcher.read_receipt()["status"] == "complete"
