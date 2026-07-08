from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from .conftest import TEST_HASH, ReleaseCli, assert_ok

WATCHER_MODES = [
    "systemd",
    "openrc",
    "runit",
    "supervisord",
    "launchd",
    "windows-task",
    "docker",
]


def json_data(result) -> dict:
    """Return a CLI JSON payload."""
    return json.loads(result.stdout)


def normalized_output(result) -> str:
    """Return CLI output with display wrapping removed."""
    return " ".join(result.output.split())


@pytest.mark.release_validation
@pytest.mark.parametrize("mode", WATCHER_MODES)
def test_each_watcher_backend_writes_state_file_and_uninstalls_cleanly(
    cli: ReleaseCli,
    tmp_path: Path,
    mode: str,
) -> None:
    """Every watcher backend should render, persist metadata, and uninstall safely."""
    root = tmp_path / f"{mode}-project"

    assert_ok(cli.run("init", mode.replace("-", ""), "--root", root, "--yes"))
    install = cli.run("watcher", "install", "--watcher", mode, "--no-activate", "--yes")
    status = cli.run("watcher", "status", "--json")
    status_data = json_data(status)
    watcher_path = Path(status_data["path"])

    assert_ok(install)
    assert_ok(status)
    assert status_data["installed"] is True
    assert status_data["mode"] == mode
    assert status_data["activated"] is False
    assert watcher_path.exists()
    rendered = watcher_path.read_text(encoding="utf-8")
    assert "sftpwarden" in rendered.lower()
    assert "watch" in rendered.lower()

    uninstall = cli.run("watcher", "uninstall", "--yes")
    status_after = cli.run("watcher", "status", "--json")
    status_after_data = json_data(status_after)

    assert_ok(uninstall)
    assert_ok(status_after)
    assert status_after_data["installed"] is False
    assert status_after_data["mode"] is None
    assert not watcher_path.exists()


@pytest.mark.release_validation
def test_watcher_auto_mode_resolves_to_a_real_backend_without_activation(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """Auto watcher install should resolve to an explicit backend and persist that decision."""
    root = tmp_path / "auto-project"
    assert_ok(cli.run("init", "autowatch", "--root", root, "--yes"))

    install = cli.run("watcher", "install", "--watcher", "auto", "--no-activate", "--yes")
    status = cli.run("watcher", "status", "--json")
    status_data = json_data(status)

    assert_ok(install)
    assert_ok(status)
    assert status_data["installed"] is True
    assert status_data["mode"] in WATCHER_MODES
    assert status_data["activated"] is False

    assert_ok(cli.run("watcher", "uninstall", "--yes"))


@pytest.mark.release_validation
def test_sync_targets_distinguish_local_remote_local_sync_and_remote_only(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """Sync should only target editable provider files for remote local-sync contexts."""
    local_root = tmp_path / "local"
    remote_root = tmp_path / "remote-local"

    assert_ok(cli.run("init", "local", "--root", local_root, "--yes"))
    assert_ok(
        cli.run(
            "init",
            "remote-local",
            "--remote",
            "deploy@example.com:/srv/sftpwarden-local",
            "--root",
            remote_root,
            "--provider",
            "yaml",
            "--watcher",
            "systemd",
            "--critical",
            "--skip-checks",
            "--yes",
        )
    )
    assert_ok(
        cli.run(
            "init",
            "remote-only",
            "--remote",
            "deploy@example.com:/srv/sftpwarden-remote-only",
            "--remote-only",
            "--provider",
            "yaml",
            "--critical",
            "--skip-checks",
            "--yes",
        )
    )

    contexts = cli.run("context", "ls", "--json")
    sync = cli.run("sync", "--dry-run", "--json")
    refresh = cli.run("refresh", "--all", "--dry-run", "--json")
    watcher = cli.run("watcher", "status", "--json")

    assert_ok(contexts)
    assert_ok(sync)
    assert_ok(refresh)
    assert_ok(watcher)

    context_data = json_data(contexts)["contexts"]
    assert context_data["local"]["type"] == "local"
    assert context_data["remote-local"]["storage"] == "local-sync"
    assert context_data["remote-only"]["storage"] == "remote-only"
    assert context_data["remote-only"]["root"] == ""
    assert context_data["remote-only"]["config"] == ""

    sync_targets = json_data(sync)["targets"]
    assert [target["context"] for target in sync_targets] == ["remote-local"]
    assert sync_targets[0]["local_path"].endswith("users.yaml")
    assert sync_targets[0]["remote_path"] == "/srv/sftpwarden-local/users.yaml"

    refresh_targets = {
        target["context"]: target["result"] for target in json_data(refresh)["targets"]
    }
    assert set(refresh_targets) == {"local", "remote-local", "remote-only"}
    assert "docker compose" in refresh_targets["local"]
    assert "ssh" in refresh_targets["remote-local"]
    assert "/srv/sftpwarden-local" in refresh_targets["remote-local"]
    assert "ssh" in refresh_targets["remote-only"]
    assert "/srv/sftpwarden-remote-only" in refresh_targets["remote-only"]

    watcher_targets = json_data(watcher)["targets"]
    assert [target["context"] for target in watcher_targets] == ["remote-local"]


@pytest.mark.release_validation
def test_deploy_dry_run_contracts_for_compose_remote_kube_and_helm(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """Each deploy target should produce the resources and commands it promises."""
    compose_root = tmp_path / "compose"
    remote_root = tmp_path / "remote"
    kube_root = tmp_path / "kube"
    helm_root = tmp_path / "helm"

    assert_ok(cli.run("init", "compose", "--root", compose_root, "--yes"))
    compose_plan = cli.run("deploy", "--context", "compose", "--dry-run", "--json")
    assert_ok(compose_plan)
    compose_data = json_data(compose_plan)["plan"]
    compose_commands = [" ".join(action["command"] or []) for action in compose_data["actions"]]
    assert compose_data["target"] == "compose"
    assert any(
        "docker compose -f docker-compose.yml up -d --build" in command
        for command in compose_commands
    )
    assert (compose_root / "docker-compose.yml").exists()

    assert_ok(
        cli.run(
            "init",
            "remotels",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            remote_root,
            "--provider",
            "yaml",
            "--watcher",
            "systemd",
            "--critical",
            "--skip-checks",
            "--yes",
        )
    )
    remote_plan = cli.run("deploy", "--context", "remotels", "--dry-run", "--json")
    assert_ok(remote_plan)
    remote_commands = [
        " ".join(action["command"] or []) for action in json_data(remote_plan)["plan"]["actions"]
    ]
    assert any("mkdir -p /opt/sftpwarden" in command for command in remote_commands)
    assert any(command.startswith("rsync ") for command in remote_commands)
    assert any(
        "docker compose -f docker-compose.yml pull" in command for command in remote_commands
    )
    assert any(
        "docker compose -f docker-compose.yml up -d" in command for command in remote_commands
    )

    assert_ok(
        cli.run(
            "init",
            "remoteonly",
            "--remote",
            "deploy@example.com:/srv/remoteonly",
            "--remote-only",
            "--provider",
            "yaml",
            "--critical",
            "--skip-checks",
            "--yes",
        )
    )
    remote_only_plan = cli.run("deploy", "--context", "remoteonly", "--dry-run", "--json")
    assert_ok(remote_only_plan)
    remote_only_commands = [
        " ".join(action["command"] or [])
        for action in json_data(remote_only_plan)["plan"]["actions"]
    ]
    assert any(
        "test -f /srv/remoteonly/sftpwarden.yaml" in command for command in remote_only_commands
    )
    assert any(
        "docker compose -f docker-compose.yml pull" in command for command in remote_only_commands
    )

    assert_ok(
        cli.run(
            "init",
            "kube",
            "--root",
            kube_root,
            "--deploy",
            "kube",
            "--namespace",
            "release-kube",
            "--skip-checks",
            "--yes",
        )
    )
    kube_render = cli.run("kube", "render", "--context", "kube")
    kube_plan = cli.run("deploy", "--context", "kube", "--dry-run", "--json")
    kube_user_change = cli.run(
        "user",
        "create",
        "alice",
        "--password-hash",
        TEST_HASH,
        "--context",
        "kube",
    )
    assert_ok(kube_render)
    assert_ok(kube_plan)
    assert_ok(kube_user_change)
    kube_data = json_data(kube_plan)["plan"]
    assert kube_data["target"] == "kubernetes"
    assert kube_data["mode"] == "manifests"
    assert kube_data["namespace"] == "release-kube"
    assert "StatefulSet" in kube_render.stdout
    assert "sftpwarden deploy" in normalized_output(kube_user_change)

    assert_ok(
        cli.run(
            "init",
            "helm",
            "--root",
            helm_root,
            "--deploy",
            "helm",
            "--namespace",
            "release-helm",
            "--skip-checks",
            "--yes",
        )
    )
    helm_values = cli.run("helm", "values", "--context", "helm", "--write")
    helm_plan = cli.run("deploy", "--context", "helm", "--dry-run", "--json")
    helm_user_change = cli.run(
        "user",
        "create",
        "bob",
        "--password-hash",
        TEST_HASH,
        "--context",
        "helm",
    )
    assert_ok(helm_values)
    assert_ok(helm_plan)
    assert_ok(helm_user_change)
    helm_data = json_data(helm_plan)["plan"]
    values = yaml.safe_load((helm_root / "values.yaml").read_text(encoding="utf-8"))
    assert helm_data["target"] == "kubernetes"
    assert helm_data["mode"] == "helm"
    assert helm_data["namespace"] == "release-helm"
    assert values["kubernetes"]["namespace"] == "release-helm"
    assert "sftpwarden helm upgrade --install" in normalized_output(helm_user_change)
