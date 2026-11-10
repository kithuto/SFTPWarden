"""Release discovery and management/deployment update boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from sftpwarden.cli import app
from sftpwarden.cli_commands import upgrade as commands
from sftpwarden.config import load_config, write_config
from sftpwarden.services import deployment_upgrade as deploy
from sftpwarden.services import installation, release_fetch, releases, upgrade, upgrade_worker
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.images import official_image
from sftpwarden.watcher import upgrade as watcher


@pytest.fixture(autouse=True)
def isolated_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))


def metadata_for(versions):
    packages = {"info": {"name": "sftpwarden"}, "releases": {}}
    public = []
    for version in versions:
        packages["releases"][version] = [
            {"packagetype": "bdist_wheel", "requires_python": ">=3.11"}
        ]
        public.append(
            {
                "tag_name": "v" + version,
                "published_at": "2026-11-10",
                "assets": [{"name": "artifact-manifest.json"}],
            }
        )
    return packages, public


@pytest.mark.parametrize(
    "version,has_inventory,accepted",
    [
        ("1.3.0", False, True),
        ("1.4.0", False, False),
        ("1.4.0", True, True),
        ("1.5.0", False, False),
    ],
)
def test_artifact_inventory_required_from_first_coordinated_release(
    version, has_inventory, accepted
):
    packages, public = metadata_for([version])
    if not has_inventory:
        public[0]["assets"] = []
    if accepted:
        assert releases.select_release(packages, public, version) == version
    else:
        with pytest.raises(SFTPWardenError, match="No completed stable release"):
            releases.select_release(packages, public, version)


def test_release_ordering_and_partial_publication():
    packages, public = metadata_for(["1.4.0", "1.9.0", "1.10.0", "2.0.0rc1", "2.0.0"])
    public[-1]["assets"] = []
    assert releases.select_release(packages, public, None) == "1.10.0"
    packages["releases"]["1.10.0"][0]["yanked"] = True
    assert releases.select_release(packages, public, None) == "1.9.0"
    packages["releases"]["1.9.0"][0]["requires_python"] = ">=99"
    assert releases.select_release(packages, public, None) == "1.4.0"
    with pytest.raises(SFTPWardenError):
        releases.select_release(packages, public, "1.10.0")
    public[0]["draft"] = True
    with pytest.raises(SFTPWardenError):
        releases.select_release(packages, public, None)
    packages["info"]["name"] = "other"
    with pytest.raises(ValueError):
        releases.select_release(packages, public, None)


@pytest.mark.parametrize(
    "value", ["https://invalid", "1.4.0rc1", "1.4.0.dev1", "1.4.0+local", "1!1.4.0"]
)
def test_invalid_upgrade_targets_are_not_requirements(value):
    with pytest.raises(SFTPWardenError):
        releases.stable_version(value)


def test_discovery_cache_and_fresh_check(monkeypatch):
    calls = []

    def fetch(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"version":"1.4.0"}')

    monkeypatch.setattr(releases.subprocess, "run", fetch)
    assert releases.discover_release()["version"] == "1.4.0"
    assert releases.discover_release()["version"] == "1.4.0"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] <= 1.7
    assert releases.discover_release(fresh=True)["status"] == "available"
    assert len(calls) == 2
    assert releases.discover_release(target="1.3.0")["status"] == "unavailable"
    assert releases.discover_release()["version"] == "1.4.0"


@pytest.mark.parametrize("failure", ["timeout", "malformed", "exit", "unavailable"])
def test_failed_discovery_is_not_up_to_date_and_is_cached(monkeypatch, failure):
    calls = []

    def fetch(command, **kwargs):
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1.7)
        if failure == "unavailable":
            raise OSError("offline")
        return SimpleNamespace(returncode=int(failure == "exit"), stdout="invalid")

    monkeypatch.setattr(releases.subprocess, "run", fetch)
    assert releases.discover_release()["status"] == "unavailable"
    assert releases.discover_release()["status"] == "unavailable"
    assert len(calls) == 1
    assert releases.discover_release(fresh=True)["status"] == "unavailable"
    assert len(calls) == 2


def test_corrupt_expired_and_wrong_python_cache_are_rechecked(monkeypatch):
    monkeypatch.setattr(
        releases.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout='{"version":"1.4.0"}'),
    )
    path = releases.cache_path()
    path.parent.mkdir(parents=True)
    for content in (
        "broken",
        "[]",
        json.dumps({"checked_at": 0, "status": "available", "python": []}),
    ):
        path.write_text(content)
        assert releases.discover_release()["version"] == "1.4.0"


def test_fetch_rejects_redirects_and_unapproved_urls():
    with pytest.raises(ValueError):
        release_fetch.fetch("file:///etc/passwd")
    with pytest.raises(ValueError):
        release_fetch.NoRedirect().redirect_request(
            None, None, 302, None, None, "https://elsewhere"
        )


def test_fetch_worker_hides_errors(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["worker"])
    monkeypatch.setattr(
        release_fetch, "fetch", lambda _: (_ for _ in ()).throw(ValueError("secret"))
    )
    assert release_fetch.main() == 1
    assert "secret" not in capsys.readouterr().out
    packages, public = metadata_for(["1.4.0"])
    monkeypatch.setattr(
        release_fetch,
        "fetch",
        lambda url: (
            packages
            if url == releases.PYPI_PROJECT
            else public
            if url == releases.GITHUB_RELEASES
            else {
                "format": 1,
                "release": "1.4.0",
                "minimum_upgrade_runtime": "1.3.0",
                "watcher_minimum_runtime": "1.4.0",
                "watcher_requires_provider_directory": True,
            }
        ),
    )
    assert release_fetch.main() == 0
    assert json.loads(capsys.readouterr().out)["version"] == "1.4.0"


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("ghcr.io/kithuto/sftpwarden:1.3.0", True),
        ("ghcr.io/kithuto/sftpwarden@sha256:abc", True),
        ("ghcr.io/kithuto/sftpwarden-custom:1.4.0", False),
        ("registry.invalid:443/kithuto/sftpwarden:1.3.0", False),
    ],
)
def test_only_official_images_are_automatically_advanced(reference, expected):
    assert official_image(reference) is expected


def test_upgrade_check_does_not_inspect_or_change_installations(monkeypatch):
    monkeypatch.setattr(
        upgrade, "discover_release", lambda **kw: {"status": "available", "version": "9.0.0"}
    )
    monkeypatch.setattr(
        watcher, "watcher_upgrade_plan", lambda *_: pytest.fail("watcher inspected")
    )
    monkeypatch.setattr(
        upgrade, "installation_plan", lambda *_: pytest.fail("installation planned")
    )
    result = CliRunner().invoke(app, ["upgrade", "--check", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["deployments"]["status"] == "not_checked"


@pytest.mark.parametrize(
    "args,status",
    [
        (["--json"], "confirmation_required"),
        (["--dry-run", "--json"], None),
        (["--yes", "--json"], "complete"),
    ],
)
def test_upgrade_confirmation_and_deferred_servers(monkeypatch, args, status):
    plan = {
        "cli": {"previous": "1.3.0", "target": "1.4.0"},
        "watcher": {"status": "update_required"},
    }
    monkeypatch.setattr(commands, "upgrade_plan", lambda *_args, **_kwargs: plan)
    calls = []
    monkeypatch.setattr(
        commands,
        "apply_management_update",
        lambda *a, **k: calls.append(1) or {"status": "complete", "deployments": "deferred"},
    )
    result = CliRunner().invoke(app, ["upgrade", *args])
    data = json.loads(result.output)
    assert data.get("status") == status
    assert bool(calls) == ("--yes" in args)
    assert result.exit_code == (1 if status == "confirmation_required" else 0)


def test_upgrade_unavailable_and_downgrade_are_errors(monkeypatch):
    monkeypatch.setattr(upgrade, "discover_release", lambda **kw: {"status": "unavailable"})
    result = CliRunner().invoke(app, ["upgrade", "--check", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "failed"
    monkeypatch.setattr(
        upgrade, "discover_release", lambda **kw: {"status": "available", "version": "1.0.0"}
    )
    with pytest.raises(SFTPWardenError, match="downgrade"):
        upgrade.upgrade_plan("1.0.0")


def test_management_resume_updates_watcher_without_deploy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        watcher, "upgrade_watcher", lambda: calls.append("watcher") or {"status": "complete"}
    )
    plan = {"cli": {"previous": get_version(), "target": get_version(), "changed": False}}
    result = upgrade.apply_management_update(plan, json_output=True)
    assert calls == ["watcher"]
    assert result["deployments"]["status"] == "deferred"
    assert json.loads(upgrade.receipt_path().read_text())["status"] == "complete"
    with pytest.raises(SFTPWardenError):
        upgrade.finish_management_update("99.0.0")


def test_installer_rejects_source_checkout_before_mutation():
    with pytest.raises(SFTPWardenError, match="source"):
        installation.installation_plan("1.4.0")


def test_installer_worker_never_exposes_package_index_errors(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {"plan": {"cli": {"target": "1.4.0"}, "installation": {"command": ["fake-pip"]}}}
        )
    )
    monkeypatch.setattr(
        upgrade_worker.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="private-token"),
    )
    monkeypatch.setattr(sys, "argv", ["worker", str(path)])
    assert upgrade_worker.main() == 1
    assert "private-token" not in path.read_text()
    assert json.loads(path.read_text())["status"] == "incomplete"


@pytest.mark.parametrize(
    "version,kind", [(None, "install"), ("1.3.0", "upgrade"), (get_version(), "redeploy")]
)
def test_deploy_plans_real_versions_without_changing_files(
    local_project_factory, monkeypatch, version, kind
):
    root, entry = local_project_factory()
    before = Path(entry.config).read_bytes()
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: version)
    plan = deploy.plan_upgrade(entry)
    assert plan.kind == kind
    assert Path(entry.config).read_bytes() == before
    assert not deploy.receipt_path(entry).exists()


@pytest.mark.parametrize("version", ["0.9.0", "9.0.0"])
def test_unknown_chains_and_downgrades_do_not_mutate(local_project_factory, monkeypatch, version):
    _, entry = local_project_factory()
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: version)
    with pytest.raises(SFTPWardenError):
        deploy.plan_upgrade(entry)
    assert not deploy.receipt_path(entry).exists()


def test_failed_activation_retains_pending_receipt(local_project_factory, monkeypatch):
    from sftpwarden.services import update_artifacts

    monkeypatch.setattr(update_artifacts, "preflight_runtime", lambda *a: None)
    _, entry = local_project_factory()
    config = load_config(entry.config)
    config.docker.image = "ghcr.io/kithuto/sftpwarden:1.3.0"
    write_config(entry.config, config)
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: "1.3.0")
    plan = deploy.plan_upgrade(entry)
    deploy.begin_upgrade(entry, plan)
    assert load_config(entry.config).docker.image.endswith(":" + get_version())
    with pytest.raises(SFTPWardenError, match="incomplete"):
        deploy.complete_upgrade(entry, plan)
    assert json.loads(deploy.receipt_path(entry).read_text())["status"] == "pending"
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: get_version())
    monkeypatch.setattr(watcher, "upgrade_watcher", lambda: {"status": "current"})
    assert deploy.complete_upgrade(entry, plan)["status"] == "complete"


def test_inspection_failure_is_not_fresh_install(local_project_factory, monkeypatch):
    _, entry = local_project_factory()
    monkeypatch.setattr(
        deploy, "run", lambda command, **kw: CommandResult(command, 1, "", "private-error")
    )
    with pytest.raises(SFTPWardenError, match="inspected"):
        deploy.deployed_version(entry)


def test_compose_inspection_and_multiple_runtime_guard(local_project_factory, monkeypatch):
    _, entry = local_project_factory()
    replies = iter(["container", "1.3.0"])
    monkeypatch.setattr(deploy, "request", lambda *a, **k: next(replies))
    assert deploy.deployed_version(entry) == "1.3.0"
    monkeypatch.setattr(deploy, "request", lambda *a, **k: "one\ntwo")
    with pytest.raises(SFTPWardenError, match="Multiple"):
        deploy.deployed_version(entry)


def test_inactive_watcher_update_keeps_checkpoints_and_never_starts(tmp_path, monkeypatch):
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode="systemd", activate=False)
    checkpoints = tmp_path / "home" / "watcher" / "checkpoints"
    checkpoints.mkdir()
    checkpoint = checkpoints / "retained.json"
    checkpoint.write_text('{"applied":"retained"}')
    monkeypatch.setattr(watcher, "_execute", lambda _: pytest.fail("inactive watcher activated"))
    assert watcher.upgrade_watcher()["activated"] is False
    assert checkpoint.read_text() == '{"applied":"retained"}'
    assert watcher.watcher_upgrade_plan()["changed"] is False


def test_watcher_failed_activation_does_not_complete(tmp_path, monkeypatch):
    from sftpwarden.config.global_config import load_global_config, save_global_config
    from sftpwarden.watcher.core import install_watcher

    install_watcher(mode="systemd", activate=False)
    config = load_global_config()
    config.watcher.activated = True
    save_global_config(config)
    backend = watcher.watcher_class(watcher.WatcherInstallMode.SYSTEMD)
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    commands = []
    monkeypatch.setattr(watcher, "_execute", commands.extend)
    monkeypatch.setattr(
        watcher, "verify_process", lambda *a: (_ for _ in ()).throw(SFTPWardenError("not ready"))
    )
    with pytest.raises(SFTPWardenError, match="not ready"):
        watcher.upgrade_watcher()
    assert any("disable" in command for command in commands)
    assert json.loads(watcher.installation_receipt().read_text())["status"] == "pending"
    assert watcher.installation_receipt().with_name("previous-installation").is_file()


@pytest.mark.parametrize(
    "args,answer,allowed",
    [
        (["--yes", "--json"], None, False),
        (["--yes", "--deploy-all", "--json"], None, True),
        ([], "n\n", False),
        ([], "y\n", True),
    ],
)
def test_incompatibility_requires_explicit_all_context_consent(monkeypatch, args, answer, allowed):
    plan = {
        "cli": {"previous": "1.3.0", "target": "1.4.0"},
        "watcher": {"status": "update_required"},
        "deployments": {
            "requires_deploy_all": True,
            "contexts": [{"name": "prod"}, {"name": "staging"}],
            "incompatible": [{"context": "prod", "reason": "old runtime"}],
        },
    }
    monkeypatch.setattr(commands, "upgrade_plan", lambda *a, **kw: plan)
    changes = []
    monkeypatch.setattr(
        commands,
        "apply_management_update",
        lambda p, **kw: changes.append(p.copy()) or {"status": "complete"},
    )
    result = CliRunner().invoke(app, ["upgrade", *args], input=answer)
    assert bool(changes) is allowed
    if allowed:
        assert changes[0]["deploy_all_authorized"] is True
    assert result.exit_code == (0 if allowed else 1)
    if not args:
        assert "ALL contexts: prod, staging" in result.output
        assert "Active transfers may stop" in result.output


def test_compatibility_requires_real_runtime_evidence(local_project_factory, monkeypatch):
    from sftpwarden.config.global_config import load_global_config, save_global_config
    from sftpwarden.contexts import ContextRegistry, ContextType, RemoteEndpoint, save_registry
    from sftpwarden.services import update_compatibility as compatibility

    root, entry = local_project_factory()
    entry.type = ContextType.REMOTE
    entry.remote = RemoteEndpoint(
        host="server.invalid",
        user="operator",
        remote_root="/srv/project",
        remote_config="/srv/project/sftpwarden.yaml",
    )
    from sftpwarden.config import RemoteStorage

    entry.storage = RemoteStorage.LOCAL_SYNC
    save_registry(ContextRegistry(contexts={entry.name: entry}))
    state = load_global_config()
    state.watcher.installed = True
    save_global_config(state)
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: "1.3.0")
    result = compatibility.assess_contexts(compatibility.bundled_compatibility(), {"changed": True})
    assert result["requires_deploy_all"] is True
    assert result["incompatible"][0]["context"] == entry.name
    monkeypatch.setattr(deploy, "deployed_version", lambda *a, **kw: get_version())
    config = load_config(entry.config)
    config.provider.path = "/etc/sftpwarden/provider-data/users.yaml"
    write_config(entry.config, config)
    assert not compatibility.assess_contexts(
        compatibility.bundled_compatibility(), {"changed": True}
    )["requires_deploy_all"]
    monkeypatch.setattr(
        deploy,
        "deployed_version",
        lambda *a, **kw: (_ for _ in ()).throw(SFTPWardenError("unreachable")),
    )
    with pytest.raises(SFTPWardenError, match="unreachable"):
        compatibility.assess_contexts(compatibility.bundled_compatibility(), {"changed": True})
