"""Network, notice and platform boundaries that must not misreport update success."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.cli_commands import update_notice
from sftpwarden.services import release_fetch, releases, upgrade
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.watcher import upgrade as watcher


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))


@pytest.mark.parametrize("content", [b'{"ok":true}', b"x" * (4 * 1024 * 1024 + 1), b"invalid"])
def test_metadata_transport_bounds_size_and_parses_only_json(content, monkeypatch):
    calls = []

    def open_response(request, *, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(content)

    monkeypatch.setattr(
        release_fetch.urllib.request,
        "build_opener",
        lambda *args: SimpleNamespace(open=open_response),
    )
    if content.startswith(b"{"):
        assert release_fetch.fetch(releases.PYPI_PROJECT) == {"ok": True}
    else:
        with pytest.raises(ValueError):
            release_fetch.fetch(releases.PYPI_PROJECT)
    assert calls == [(releases.PYPI_PROJECT, 4)]


@pytest.mark.parametrize(
    "mode", ["json", "pipe", "environment", "disabled", "offline", "current", "new"]
)
def test_notices_are_optional_nonfatal_and_never_pollute_machine_output(mode, monkeypatch):
    output = []
    checks = []
    monkeypatch.setattr(
        update_notice, "console", SimpleNamespace(is_terminal=mode != "pipe", print=output.append)
    )
    monkeypatch.setattr(
        update_notice,
        "load_global_config",
        lambda: SimpleNamespace(updates=SimpleNamespace(check_enabled=mode != "disabled")),
    )
    if mode == "environment":
        monkeypatch.setenv("SFTPWARDEN_NO_UPDATE_CHECK", "1")
    else:
        monkeypatch.delenv("SFTPWARDEN_NO_UPDATE_CHECK", raising=False)

    def discover():
        checks.append(True)
        if mode == "offline":
            raise OSError("private proxy error")
        return {"status": "available", "version": "99.0.0" if mode == "new" else "1.0.0"}

    monkeypatch.setattr(update_notice, "discover_release", discover)
    update_notice.notify_update(json_output=mode == "json")
    assert bool(checks) == (mode in {"offline", "current", "new"})
    assert bool(output) == (mode == "new")
    if output:
        assert "deploy" in output[0] and "99.0.0" in output[0]


def test_dead_background_worker_does_not_remain_reported_as_running(monkeypatch):
    path = upgrade.receipt_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "running", "worker_pid": 12345}))
    monkeypatch.setattr(upgrade, "worker_running", lambda pid: False)
    assert upgrade.upgrade_status()["status"] == "incomplete"
    monkeypatch.setattr(upgrade, "worker_running", lambda pid: True)
    assert upgrade.upgrade_status()["status"] == "running"
    assert json.loads(path.read_text())["status"] == "running", "Status must be read-only"


@pytest.mark.parametrize("mode", ["systemd", "openrc", "runit", "supervisord"])
def test_native_upgrade_refuses_to_change_original_service_account(mode, monkeypatch):
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode=mode, activate=False)
    backend = watcher.watcher_class(watcher.WatcherInstallMode(mode))
    original = backend.path().read_bytes()
    rendered = backend.render()
    replacements = {
        "systemd": ("User=", "User=different-"),
        "supervisord": ("user=", "user=different-"),
        "openrc": ('command_user="', 'command_user="different-'),
        "runit": ("chpst -u ", "chpst -u different-"),
    }
    old, new = replacements[mode]
    monkeypatch.setattr(backend, "render", lambda **kw: rendered.replace(old, new))
    with pytest.raises(SFTPWardenError, match="service account"):
        watcher.watcher_upgrade_plan()
    assert backend.path().read_bytes() == original
    assert not watcher.installation_receipt().exists()


@pytest.mark.parametrize(
    "mode", ["build", "pull", "failure", "helm", "bad_chart", "invalid", "downgrade"]
)
def test_artifact_preflight_checks_candidate_before_migration(
    local_project_factory, monkeypatch, tmp_path, mode
):
    from sftpwarden.config import DeployTarget, KubernetesMode, load_config, write_config
    from sftpwarden.services import deployment_upgrade, update_artifacts

    _, entry = local_project_factory()
    config = load_config(entry.config)
    if mode in {"helm", "bad_chart"}:
        config.deploy.target = DeployTarget.KUBERNETES
        config.kubernetes.mode = KubernetesMode.HELM
    write_config(entry.config, config)
    monkeypatch.setattr(deployment_upgrade, "deployed_version", lambda *a, **kw: "1.3.0")
    plan = deployment_upgrade.plan_upgrade(entry)
    before = Path(entry.config).read_bytes()
    calls = []
    monkeypatch.setattr(
        update_artifacts,
        "runtime_image_reference",
        lambda *a, **kw: SimpleNamespace(
            image="candidate",
            build={"context": str(tmp_path), "dockerfile": "Dockerfile"}
            if mode == "build"
            else None,
        ),
    )

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=int(mode == "failure"),
            stdout="version: 1.3.1\nappVersion: " + ("1.3.0" if mode == "bad_chart" else "1.3.1"),
        )

    monkeypatch.setattr(update_artifacts, "run", run)
    monkeypatch.setattr(update_artifacts, "kubernetes_candidate", lambda *a: "1.3.1")
    monkeypatch.setattr(
        update_artifacts,
        "probe_version",
        lambda *a: "invalid" if mode == "invalid" else "1.3.0" if mode == "downgrade" else "1.3.1",
    )
    if mode in {"failure", "bad_chart", "invalid", "downgrade"}:
        with pytest.raises(SFTPWardenError):
            update_artifacts.preflight_runtime(entry, plan)
    else:
        update_artifacts.preflight_runtime(entry, plan)
    assert Path(entry.config).read_bytes() == before
    assert not deployment_upgrade.receipt_path(entry).exists()
    assert calls[0][0] == ("helm" if mode in {"helm", "bad_chart"} else "docker")


@pytest.mark.parametrize("valid", [False, True])
def test_watcher_verification_requires_two_new_heartbeats(monkeypatch, valid):
    from sftpwarden.utils._version import get_version

    clock = [100.0]
    monkeypatch.setattr(watcher.time, "time", lambda: clock[0])
    monkeypatch.setattr(watcher.time, "monotonic", lambda: clock[0])
    path = watcher.process_path()
    path.parent.mkdir(parents=True)
    path.write_text("invalid")

    def tick(_):
        clock[0] += 1 if valid else 80
        path.write_text(
            json.dumps(
                {
                    "version": get_version(),
                    "started": 100,
                    "instance": "new",
                    "heartbeat": clock[0],
                }
            )
        )

    monkeypatch.setattr(watcher.time, "sleep", tick)
    if valid:
        assert watcher.verify_process(get_version(), 100, "old")["instance"] == "new"
        assert clock[0] == 102
    else:
        with pytest.raises(SFTPWardenError, match="liveness"):
            watcher.verify_process(get_version(), 100, "old")


@pytest.mark.parametrize("custom", [False, True])
def test_docker_watcher_prepares_before_recreation_and_preserves_custom_images(monkeypatch, custom):
    from sftpwarden.config.global_config import load_global_config, save_global_config
    from sftpwarden.watcher.core import install_watcher

    image = "custom.invalid/watcher:old" if custom else "ghcr.io/kithuto/sftpwarden-watcher:1.3.0"
    install_watcher(mode="docker", image=image, activate=False)
    state = load_global_config()
    state.watcher.activated = True
    save_global_config(state)
    backend = watcher.watcher_class(watcher.WatcherInstallMode.DOCKER)
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    calls = []
    monkeypatch.setattr(watcher, "_execute", calls.extend)
    monkeypatch.setattr(watcher, "verify_process", lambda *a: {"instance": "new"})
    result = watcher.upgrade_watcher()
    if custom:
        assert result["status"] == "custom_image" and not calls
    else:
        assert calls[0][-1] == "pull"
        assert calls[1][-1] == "--force-recreate"
        assert result["status"] == "complete"


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("failed", [False, True])
def test_cli_handoff_cannot_claim_success_without_worker_verification(
    monkeypatch, platform, failed
):
    from sftpwarden.utils._version import get_version

    monkeypatch.setattr(upgrade, "os", SimpleNamespace(name=platform, getpid=lambda: 111))
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        result = {"cli": {"applied": "1.3.2"}, "watcher": {}, "deployments": {}}
        upgrade.receipt_path().write_text(
            json.dumps(
                {
                    "status": "incomplete" if failed else "complete",
                    "result": result,
                }
            )
        )
        return SimpleNamespace(returncode=int(failed))

    monkeypatch.setattr(upgrade.subprocess, "run", run)
    monkeypatch.setattr(
        upgrade.subprocess,
        "Popen",
        lambda command, **kw: commands.append(command) or SimpleNamespace(pid=222),
    )
    plan = {"cli": {"previous": get_version(), "target": "1.3.2", "changed": True}}
    if failed and platform == "posix":
        with pytest.raises(SFTPWardenError, match="incomplete"):
            upgrade.apply_management_update(plan, json_output=True)
    else:
        result = upgrade.apply_management_update(plan, json_output=True)
        if platform == "nt":
            assert result["status"] == "running"
            assert "--parent" in commands[0][-1]
            assert json.loads(upgrade.receipt_path().read_text())["worker_pid"] == 222
        else:
            assert result["cli"]["applied"] == "1.3.2"


@pytest.mark.parametrize("status", [0, 258, None])
def test_windows_process_handle_wait_and_close_contract(monkeypatch, status):
    import ctypes

    from sftpwarden.services import upgrade_worker

    closed = []

    class OpenProcess:
        def __call__(self, *args):
            return 123 if status is not None else 0

    kernel = SimpleNamespace(
        OpenProcess=OpenProcess(),
        WaitForSingleObject=lambda *args: status,
        CloseHandle=lambda handle: closed.append(handle.value),
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setattr(upgrade, "os", SimpleNamespace(name="nt"))
    assert upgrade.worker_running(456) == (status == 258)
    if status == 258:
        with pytest.raises(RuntimeError, match="did not exit"):
            upgrade_worker.wait_parent(456)
    else:
        upgrade_worker.wait_parent(456)
    assert closed == ([123, 123] if status is not None else [])


def test_coordinated_completion_waits_for_all_contexts_and_watcher(
    local_project_factory, monkeypatch
):
    from sftpwarden.contexts import ContextRegistry, save_registry
    from sftpwarden.services import deploy, deployment_upgrade, update_compatibility
    from sftpwarden.utils._version import get_version

    _, first = local_project_factory(name="first")
    _, second = local_project_factory(name="second")
    save_registry(ContextRegistry(contexts={entry.name: entry for entry in (first, second)}))
    contexts = [
        {"name": entry.name, "identity": update_compatibility.context_identity(entry)}
        for entry in (first, second)
    ]
    calls = []
    monkeypatch.setattr(deployment_upgrade, "deployed_version", lambda *a, **kw: get_version())
    monkeypatch.setattr(deploy, "apply_deployment_plan", lambda entry: calls.append(entry.name))

    def activate():
        assert calls == ["first", "second"]
        for entry in (first, second):
            assert (
                json.loads(deployment_upgrade.receipt_path(entry).read_text())["status"]
                == "runtime_ready"
            )
        return {"status": "complete"}

    monkeypatch.setattr(watcher, "upgrade_watcher", activate)
    result = upgrade.finish_management_update(get_version(), deploy_contexts=contexts)
    assert result["deployments"]["status"] == "complete"
    for entry in (first, second):
        assert (
            json.loads(deployment_upgrade.receipt_path(entry).read_text())["status"] == "complete"
        )


@pytest.mark.parametrize("state", ["not_installed", "custom_image", "inactive", "complete"])
@pytest.mark.parametrize("deferred", [False, True])
def test_human_results_distinguish_component_status_and_deferred_servers(
    state, deferred, monkeypatch
):
    from sftpwarden.cli_commands import upgrade as command

    lines = []
    monkeypatch.setattr(command, "console", SimpleNamespace(print=lines.append))
    watcher_result = {"status": state, "action": "Custom image preserved."}
    if state == "inactive":
        watcher_result["activated"] = False
    command.print_result(
        {
            "cli": {"applied": "1.3.1"},
            "watcher": watcher_result,
            "deployments": {
                "status": "deferred" if deferred else "complete",
                "results": [{"context": "prod", "applied": "1.3.1", "status": "complete"}],
            },
        }
    )
    assert any("servers have not been updated" in line for line in lines) == deferred
    if state == "inactive":
        assert any("remains inactive" in line for line in lines)
    if state == "custom_image":
        assert "Custom image preserved." in lines
