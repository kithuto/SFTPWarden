from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import sftpwarden.watcher.backends.docker as docker_backend
import sftpwarden.watcher.core as watcher_module
from sftpwarden.cli import app
from sftpwarden.config import (
    ProviderType,
    default_project_config,
    write_config,
)
from sftpwarden.config.global_config import load_global_config
from sftpwarden.contexts import (
    ContextRegistry,
    ContextType,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.providers import empty_provider_text
from sftpwarden.utils.errors import ContextError
from sftpwarden.watcher.core import (
    WatcherDockerFallbackRequired,
    WatcherInstallMode,
    default_watcher_mode,
    ensure_watcher,
    install_watcher,
    poll_watch,
    render_docker_watcher_compose,
    resolve_watcher_mode,
    run_watcher_commands,
    sync_target,
    uninstall_watcher,
    watcher_install_plan,
    watcher_uninstall_plan,
)
from sftpwarden.watcher.registry import registered_watchers


def test_watcher_install_is_idempotent_and_can_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    init = runner.invoke(app, ["init", "dev", "--root", str(tmp_path / "project"), "--yes"])
    assert init.exit_code == 0, init.output

    first = runner.invoke(
        app, ["watcher", "install", "--watcher", "systemd", "--yes", "--no-activate"]
    )
    second = runner.invoke(
        app, ["watcher", "install", "--watcher", "systemd", "--yes", "--no-activate"]
    )
    replaced = runner.invoke(
        app, ["watcher", "install", "--watcher", "docker", "--yes", "--no-activate"]
    )
    status = runner.invoke(app, ["watcher", "status"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "already installed" in second.output
    assert replaced.exit_code == 0, replaced.output
    assert load_global_config().watcher.mode == "docker"
    assert "Watcher installed: True" in status.output
    assert "Watcher mode: docker" in status.output
    watcher_path = Path(load_global_config().watcher.path or "")
    assert "/var/run/docker.sock" not in watcher_path.read_text(encoding="utf-8")


def test_default_and_existing_watcher_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_module,
        "detect_native_watcher_mode",
        lambda: WatcherInstallMode.SYSTEMD,
    )

    assert default_watcher_mode() == WatcherInstallMode.AUTO
    installed = ensure_watcher()

    assert installed.startswith("Installed systemd watcher")
    assert ensure_watcher() == "Using existing systemd watcher."


def test_registered_watcher_backends_cover_supported_modes() -> None:
    modes = set(registered_watchers())

    assert modes == {
        WatcherInstallMode.SYSTEMD,
        WatcherInstallMode.OPENRC,
        WatcherInstallMode.RUNIT,
        WatcherInstallMode.SUPERVISORD,
        WatcherInstallMode.LAUNCHD,
        WatcherInstallMode.WINDOWS_TASK,
        WatcherInstallMode.DOCKER,
    }


def test_auto_watcher_resolves_detected_native_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_module,
        "detect_native_watcher_mode",
        lambda: WatcherInstallMode.OPENRC,
    )

    assert resolve_watcher_mode() == WatcherInstallMode.OPENRC
    message = install_watcher(mode="auto", yes=True, activate=False)

    assert message.startswith("Installed openrc watcher")
    assert load_global_config().watcher.mode == "openrc"


def test_auto_watcher_requires_consent_before_docker_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(watcher_module, "detect_native_watcher_mode", lambda: None)

    with pytest.raises(WatcherDockerFallbackRequired, match="No supported native"):
        resolve_watcher_mode()

    message = install_watcher(
        mode="auto",
        yes=True,
        activate=False,
        allow_docker_fallback=True,
    )

    assert message.startswith("Installed docker watcher")
    assert load_global_config().watcher.mode == "docker"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (WatcherInstallMode.OPENRC, "rc-service"),
        (WatcherInstallMode.RUNIT, "/etc/sv/sftpwarden-watch"),
        (WatcherInstallMode.SUPERVISORD, "supervisorctl"),
        (WatcherInstallMode.LAUNCHD, "launchctl"),
        (WatcherInstallMode.WINDOWS_TASK, "schtasks"),
    ],
)
def test_native_watcher_install_plans_render_expected_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: WatcherInstallMode,
    expected: str,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))

    plan = watcher_install_plan(mode)

    assert plan.mode == mode
    assert expected in plan.text()


def test_install_watcher_can_activate_plan_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_module.watcher_class(WatcherInstallMode.SYSTEMD),
        "is_supported",
        classmethod(lambda cls: True),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(watcher_module, "run_watcher_commands", calls.extend)

    message = install_watcher(mode="systemd", yes=True, activate=True)

    assert message.startswith("Installed systemd watcher")
    assert calls == watcher_install_plan(WatcherInstallMode.SYSTEMD).commands


def test_install_watcher_rejects_replacement_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    install_watcher(mode="systemd", yes=True, activate=False)

    with pytest.raises(ContextError, match="already installed in systemd mode"):
        install_watcher(mode="docker", yes=False, activate=False)


def test_uninstall_watcher_reports_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))

    assert uninstall_watcher() == "Watcher is not installed."


def test_uninstall_watcher_dry_run_keeps_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    install_watcher(mode="systemd", yes=True, activate=False)

    message = uninstall_watcher(dry_run=True)

    assert message.startswith("Would uninstall systemd watcher")
    assert load_global_config().watcher.installed


def test_uninstall_watcher_runs_backend_commands_when_activated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_module.watcher_class(WatcherInstallMode.SYSTEMD),
        "is_supported",
        classmethod(lambda cls: True),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(watcher_module, "run_watcher_commands", calls.extend)
    install_watcher(mode="systemd", yes=True, activate=True)
    calls.clear()

    message = uninstall_watcher()

    assert message == "Watcher uninstalled."
    assert calls == watcher_uninstall_plan(WatcherInstallMode.SYSTEMD).commands
    assert not load_global_config().watcher.installed


def test_uninstall_watcher_skips_commands_when_not_activated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    calls: list[list[str]] = []
    monkeypatch.setattr(watcher_module, "run_watcher_commands", calls.extend)
    install_watcher(mode="systemd", yes=True, activate=False)

    uninstall_watcher()

    assert calls == []
    assert not load_global_config().watcher.installed


def test_install_watcher_replacement_deactivates_old_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_module.watcher_class(WatcherInstallMode.SYSTEMD),
        "is_supported",
        classmethod(lambda cls: True),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(watcher_module, "run_watcher_commands", calls.extend)
    install_watcher(mode="systemd", yes=True, activate=True)
    calls.clear()

    message = install_watcher(mode="docker", yes=True, activate=False)

    assert message.startswith("Installed docker watcher")
    assert calls == watcher_uninstall_plan(WatcherInstallMode.SYSTEMD).commands
    assert load_global_config().watcher.mode == "docker"


def test_run_watcher_commands_runs_each_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        watcher_module, "run_checked", lambda command, **_kwargs: calls.append(command)
    )

    run_watcher_commands([["sudo", "systemctl", "daemon-reload"], ["sudo", "systemctl", "restart"]])

    assert calls == [["sudo", "systemctl", "daemon-reload"], ["sudo", "systemctl", "restart"]]


def test_docker_watcher_plan_uses_custom_image() -> None:
    plan = watcher_install_plan(WatcherInstallMode.DOCKER, image="example/watcher:test")
    text = render_docker_watcher_compose(image="example/watcher:test")

    assert "example/watcher:test" in text
    assert "docker compose" in plan.text()
    assert "--build" not in plan.text()


def test_docker_watcher_image_resolves_local_pip_and_custom_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Docker watcher builds source images and pulls packaged GHCR images."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(docker_backend, "docker_watcher_ssh_volumes", lambda: [])

    local = render_docker_watcher_compose()
    local_plan = watcher_install_plan(WatcherInstallMode.DOCKER)

    assert "image: sftpwarden-watcher:local" in local
    assert "dockerfile: docker/watcher/Dockerfile" in local
    assert "pull" not in local_plan.text()
    assert "--build" in local_plan.text()

    monkeypatch.setattr(docker_backend, "LOCAL_WATCHER_DOCKERFILE", tmp_path / "missing")
    monkeypatch.setattr(docker_backend, "get_version", lambda: "9.9.9")

    packaged = render_docker_watcher_compose()
    packaged_plan = watcher_install_plan(WatcherInstallMode.DOCKER)

    assert "image: ghcr.io/kithuto/sftpwarden-watcher:9.9.9" in packaged
    assert "dockerfile:" not in packaged
    assert "docker compose" in packaged_plan.text()
    assert "pull" in packaged_plan.text()
    assert "--build" not in packaged_plan.text()

    custom = render_docker_watcher_compose(image="registry.example.com/watcher:test")
    custom_plan = watcher_install_plan(
        WatcherInstallMode.DOCKER, image="registry.example.com/watcher:test"
    )

    assert "image: registry.example.com/watcher:test" in custom
    assert "dockerfile:" not in custom
    assert "pull" not in custom_plan.text()
    assert "--build" not in custom_plan.text()


def test_poll_watch_syncs_changed_targets_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
    users_file = project / "users.yaml"
    users_file.write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    calls: list[tuple[str, Path, str, bool]] = []

    def stop_after_first_sleep(_interval: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        watcher_module,
        "sync_target",
        lambda context, local_path, remote_path, *, dry_run=False: calls.append(
            (context.name, local_path, remote_path, dry_run)
        ),
    )
    monkeypatch.setattr(watcher_module.time, "sleep", stop_after_first_sleep)

    with pytest.raises(KeyboardInterrupt):
        poll_watch(interval_seconds=1, dry_run=True)

    assert calls == [("prod", users_file, "/opt/sftpwarden/users.yaml", True)]


def test_docker_watcher_mounts_only_explicit_ssh_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    user_home = tmp_path / "user-home"
    key_path = user_home / ".ssh" / "sftpwarden_deploy"
    known_hosts = user_home / ".ssh" / "known_hosts"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("private-key", encoding="utf-8")
    known_hosts.write_text("example.com ssh-ed25519 AAAA\n", encoding="utf-8")
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    project = tmp_path / "prod-project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
    (project / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=str(key_path),
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))

    ssh_volumes = watcher_module.docker_watcher_ssh_volumes()
    rendered = render_docker_watcher_compose()
    volumes = yaml.safe_load(rendered)["services"]["sftpwarden-watcher"]["volumes"]
    sources = {volume["source"] for volume in volumes}
    targets = {volume["target"] for volume in volumes}

    assert all(isinstance(volume, dict) for volume in ssh_volumes)
    assert {volume["source"] for volume in ssh_volumes} == {str(key_path), str(known_hosts)}
    assert str(project) in sources
    assert str(key_path) in sources
    assert str(known_hosts) in sources
    assert str(user_home / ".ssh") not in sources
    assert "/workspace/prod-" in rendered
    assert "/run/sftpwarden-watcher/ssh/prod-" in rendered
    assert "/run/sftpwarden-watcher/known_hosts" in targets


def test_docker_watcher_rejects_default_ssh_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "prod-project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
    (project / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key="default",
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))

    with pytest.raises(ContextError, match="Docker watcher cannot use the host default SSH"):
        render_docker_watcher_compose()


def test_docker_watcher_rejects_missing_explicit_ssh_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "prod-project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
    (project / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=str(tmp_path / "missing-key"),
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))

    with pytest.raises(ContextError, match="Docker watcher SSH key not found"):
        render_docker_watcher_compose()


def test_watcher_status_json_reports_state_and_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    init = runner.invoke(app, ["init", "dev", "--root", str(tmp_path / "project"), "--yes"])
    assert init.exit_code == 0, init.output
    runner.invoke(app, ["watcher", "install", "--watcher", "systemd", "--yes", "--no-activate"])

    result = runner.invoke(app, ["watcher", "status", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["installed"]
    assert data["mode"] == "systemd"
    assert data["targets"] == []


def test_watcher_uninstall_clears_global_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    init = runner.invoke(app, ["init", "dev", "--root", str(tmp_path / "project"), "--yes"])
    assert init.exit_code == 0, init.output
    runner.invoke(app, ["watcher", "install", "--watcher", "systemd", "--yes", "--no-activate"])

    result = runner.invoke(app, ["watcher", "uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    assert not load_global_config().watcher.installed


def test_systemd_watcher_install_plan_uses_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    init = runner.invoke(app, ["init", "dev", "--root", str(tmp_path / "project"), "--yes"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(
        app,
        ["watcher", "install", "--watcher", "systemd", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "sudo install" in result.output
    assert "sudo systemctl enable --now sftpwarden-watch.service" in result.output


def test_context_add_remote_local_sync_auto_installs_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_module,
        "detect_native_watcher_mode",
        lambda: WatcherInstallMode.SYSTEMD,
    )
    root = tmp_path / "prod-project"
    root.mkdir()
    config = default_project_config("prod")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "context",
            "add",
            "prod",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            str(root),
            "--critical",
            "--skip-checks",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_global_config().watcher.installed
    assert load_global_config().watcher.mode == "systemd"


def test_context_remove_uninstalls_watcher_when_no_local_sync_targets_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "prod-project"
    root.mkdir()
    config = default_project_config("prod")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    install_watcher(mode="systemd", yes=True, activate=False)

    result = CliRunner().invoke(app, ["context", "remove", "prod", "--yes"])

    assert result.exit_code == 0, result.output
    assert not load_global_config().watcher.installed


def test_sync_target_escapes_ssh_key_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shlex

    monkeypatch.setattr(watcher_module, "system_is", lambda name: name == "Linux")
    key_path = tmp_path / "id deploy;rm"
    key_path.write_text("private-key", encoding="utf-8")
    local_file = tmp_path / "sftpwarden.yaml"
    local_file.write_text("version: 1\n", encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=str(key_path),
        critical=True,
    )

    rendered = sync_target(entry, local_file, "/opt/sftpwarden/sftpwarden.yaml", dry_run=True)
    args = shlex.split(rendered)
    transport = shlex.split(args[4])

    assert args[:4] == ["rsync", "-az", "--protect-args", "-e"]
    assert transport[0] == "ssh"
    assert transport[transport.index("-i") + 1] == str(key_path)


def test_sync_target_requires_remote_settings(tmp_path: Path) -> None:
    entry = local_context("dev", tmp_path / "dev-project", ProviderType.YAML)

    with pytest.raises(ContextError, match="missing remote settings"):
        sync_target(entry, tmp_path / "users.yaml", "/opt/sftpwarden/users.yaml")


def test_sync_target_runs_rsync_and_returns_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher_module, "system_is", lambda name: name == "Linux")
    local_file = tmp_path / "users.yaml"
    local_file.write_text("users: []\n", encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    calls: list[list[str]] = []

    class Result:
        stdout = "sent users\n"

    def fake_run_checked(command: list[str], **_kwargs: object) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr(watcher_module, "run_checked", fake_run_checked)

    output = sync_target(entry, local_file, "/opt/sftpwarden/users.yaml")

    assert output == "sent users"
    assert calls == [
        [
            "rsync",
            "-az",
            "--protect-args",
            "-e",
            "ssh -p 22 -o BatchMode=yes -o ConnectTimeout=10",
            str(local_file),
            "deploy@example.com:/opt/sftpwarden/users.yaml",
        ]
    ]


def test_sync_target_uses_scp_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_file = tmp_path / "users.yaml"
    local_file.write_text("users: []\n", encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    monkeypatch.setattr(watcher_module, "system_is", lambda name: name == "Windows")

    rendered = sync_target(entry, local_file, "/opt/sftpwarden/users.yaml", dry_run=True)

    assert rendered.startswith("scp ")
    assert " -P 22 " in rendered
    assert "rsync" not in rendered


def test_docker_watcher_ignores_context_without_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = local_context("dev", tmp_path / "dev", ProviderType.YAML)
    entry.type = ContextType.REMOTE
    entry.storage = "local-sync"  # type: ignore
    entry.remote = None
    monkeypatch.setattr(watcher_module, "docker_watcher_remote_contexts", lambda: [entry])

    assert watcher_module.docker_watcher_ssh_volumes() == []
