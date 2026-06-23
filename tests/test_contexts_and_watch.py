from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.refresh as refresh_module
import sftpwarden.remote.deploy as deploy_module
import sftpwarden.watcher as watcher_module
from sftpwarden.cli import app
from sftpwarden.config import (
    ProjectConfig,
    ProviderConfig,
    ProviderType,
    SFTPWardenConfig,
    default_project_config,
    write_config,
)
from sftpwarden.config.global_config import load_global_config
from sftpwarden.contexts import (
    ContextRegistry,
    ContextType,
    load_registry,
    local_context,
    parse_remote_url,
    remote_context,
    save_registry,
)
from sftpwarden.providers import empty_provider_text
from sftpwarden.refresh import refresh_context, resolve_refresh_targets
from sftpwarden.remote.deploy import deploy_context
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.errors import RuntimeError as SFTPWardenRuntimeError
from sftpwarden.watcher import (
    WatcherInstallMode,
    default_watcher_mode,
    derive_watch_targets,
    ensure_watcher,
    install_watcher,
    poll_watch,
    remote_root_path,
    render_docker_watcher_compose,
    run_watcher_commands,
    sync_target,
    uninstall_watcher,
    watcher_install_plan,
)


def test_remote_only_context_has_empty_top_level_paths() -> None:
    entry = remote_context(
        name="archive",
        provider=ProviderType.CSV,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )

    assert entry.type == ContextType.REMOTE
    assert entry.storage == "remote-only"
    assert entry.root == ""
    assert entry.config == ""
    assert not entry.watcher_required
    assert entry.remote is not None
    assert entry.remote.remote_config == "/opt/sftpwarden/sftpwarden.yaml"


def test_remote_url_allows_missing_user_with_flag() -> None:
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
        remote_user="deploy",
    )

    assert entry.remote is not None
    assert entry.remote.user == "deploy"
    assert entry.remote.host == "example.com"


def test_remote_url_rejects_user_conflict() -> None:
    with pytest.raises(ContextError, match="do not match"):
        remote_context(
            name="prod",
            provider=ProviderType.YAML,
            remote_url="deploy@example.com:/opt/sftpwarden",
            local_root=None,
            remote_root="~/sftpwarden",
            remote_only=True,
            ssh_key=None,
            critical=True,
            remote_user="other",
        )


def test_remote_url_parser_accepts_host_only() -> None:
    parsed = parse_remote_url("example.com")

    assert parsed.user is None
    assert parsed.host == "example.com"
    assert parsed.path is None


def test_watch_derives_only_user_provider_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
    (project / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

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

    targets = derive_watch_targets()

    assert {target.local_path.name for target in targets} == {"users.yaml"}
    assert {target.remote_path for target in targets} == {"/opt/sftpwarden/users.yaml"}


def test_remote_root_path_requires_remote_settings(tmp_path: Path) -> None:
    entry = local_context("dev", tmp_path / "dev", ProviderType.YAML)

    with pytest.raises(ContextError, match="missing remote settings"):
        remote_root_path(entry, tmp_path / "dev" / "users.yaml")


def test_remote_root_path_requires_local_root() -> None:
    entry = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    entry.root = ""

    with pytest.raises(ContextError, match="missing local root"):
        remote_root_path(entry, Path("users.yaml"))


def test_sync_json_lists_watch_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    project = tmp_path / "project"
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
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))

    result = CliRunner().invoke(app, ["sync", "--json", "--dry-run"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["dry_run"]
    assert {target["remote_path"] for target in data["targets"]} == {"/opt/sftpwarden/users.yaml"}


def test_watch_uses_provider_path_from_context_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    config = SFTPWardenConfig(
        project=ProjectConfig(name="prod"),
        provider=ProviderConfig(type=ProviderType.CSV, path="/etc/sftpwarden/accounts.csv"),
    )
    write_config(project / "sftpwarden.yaml", config)
    (project / "accounts.csv").write_text(
        empty_provider_text(config.provider.type), encoding="utf-8"
    )
    (project / "users.yaml").write_text("users: []\n", encoding="utf-8")

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

    targets = derive_watch_targets()

    assert {target.local_path.name for target in targets} == {"accounts.csv"}
    assert {target.remote_path for target in targets} == {"/opt/sftpwarden/accounts.csv"}


def test_watch_sql_provider_has_no_user_file_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    config = SFTPWardenConfig(
        project=ProjectConfig(name="prod"),
        provider=ProviderConfig(type=ProviderType.MYSQL, dsn="mysql://user:pass@db/sftp"),
    )
    write_config(project / "sftpwarden.yaml", config)
    (project / "users.yaml").write_text("users: []\n", encoding="utf-8")

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

    targets = derive_watch_targets()

    assert targets == []


def test_watch_skips_missing_local_provider_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
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

    targets = derive_watch_targets()

    assert targets == []


def test_watch_derivation_skips_non_sync_or_incomplete_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    config = default_project_config("prod")
    write_config(project / "sftpwarden.yaml", config)
    (project / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    local = local_context("local", project, ProviderType.YAML)
    missing_root = remote_context(
        name="missing-root",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    missing_root.root = ""
    missing_config = remote_context(
        name="missing-config",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path / "missing",
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(
        ContextRegistry(
            default="local",
            contexts={
                "local": local,
                "missing-root": missing_root,
                "missing-config": missing_config,
            },
        )
    )

    assert derive_watch_targets() == []


def test_refresh_all_resolves_registered_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    one = local_context("one", tmp_path / "one", ProviderType.YAML)
    two = local_context("two", tmp_path / "two", ProviderType.YAML)
    save_registry(ContextRegistry(default="one", contexts={"one": one, "two": two}))

    targets = resolve_refresh_targets(all_contexts=True)

    assert [target.name for target in targets] == ["one", "two"]


def test_refresh_json_reports_dry_run_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SFTPWARDEN_HOME", str(home))
    context = local_context("dev", tmp_path / "dev-project", ProviderType.YAML)
    save_registry(ContextRegistry(default="dev", contexts={"dev": context}))

    result = CliRunner().invoke(app, ["refresh", "--dry-run", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["dry_run"]
    assert data["targets"][0]["context"] == "dev"
    assert "docker compose" in data["targets"][0]["result"]


def test_refresh_all_requires_registered_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))

    with pytest.raises(ContextError, match="No SFTPWarden context has been initialized"):
        resolve_refresh_targets(all_contexts=True)

    project = tmp_path / "project"
    project.mkdir()
    write_config(project / "sftpwarden.yaml", default_project_config("dev"))
    monkeypatch.chdir(project)
    save_registry(ContextRegistry())
    with pytest.raises(ContextError, match="No contexts are registered"):
        resolve_refresh_targets(all_contexts=True)


def test_remote_default_ssh_key_uses_ssh_defaults() -> None:
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key="default",
        critical=True,
    )

    command = refresh_context(entry, dry_run=True)

    assert " -i " not in command
    assert "deploy@example.com" in command


def test_refresh_uses_non_tty_docker_exec(tmp_path: Path) -> None:
    entry = local_context("dev", tmp_path / "sftpwarden-dev", ProviderType.YAML)

    command = refresh_context(entry, dry_run=True)

    assert "exec -T sftpwarden" in command


def test_init_remote_creates_local_sync_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "prod-project"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "remote",
            "--context",
            "prod",
            "--remote-url",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            str(root),
            "--critical",
            "--skip-checks",
            "--yes",
        ],
    )

    registry = load_registry()
    entry = registry.contexts["prod"]

    assert result.exit_code == 0, result.output
    assert (root / "sftpwarden.yaml").exists()
    assert (root / "users.yaml").exists()
    assert (root / "docker-compose.yml").exists()
    assert entry.type == ContextType.REMOTE
    assert entry.storage == "local-sync"
    assert entry.remote is not None
    assert entry.remote.remote_root == "/opt/sftpwarden"
    assert load_global_config().watcher.installed
    assert load_global_config().watcher.mode == "systemd"
    assert registry.default == "prod"


def test_init_remote_shortcut_creates_local_sync_context_from_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "prod-project"
    root.mkdir()
    monkeypatch.chdir(root)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "prod",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--critical",
            "--skip-checks",
            "--yes",
        ],
    )

    registry = load_registry()
    entry = registry.contexts["prod"]

    assert result.exit_code == 0, result.output
    assert (root / "sftpwarden.yaml").exists()
    assert (root / "users.yaml").exists()
    assert (root / "docker-compose.yml").exists()
    assert registry.default == "prod"
    assert entry.type == ContextType.REMOTE
    assert entry.storage == "local-sync"
    assert entry.root == str(root)
    assert entry.remote is not None
    assert entry.remote.remote_root == "/opt/sftpwarden"


def test_init_remote_only_does_not_install_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "remote",
            "--context",
            "archive",
            "--remote-url",
            "deploy@example.com:/opt/sftpwarden",
            "--remote-only",
            "--critical",
            "--skip-checks",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not load_global_config().watcher.installed


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

    assert default_watcher_mode() == WatcherInstallMode.SYSTEMD
    installed = ensure_watcher()

    assert installed.startswith("Installed systemd watcher")
    assert ensure_watcher() == "Using existing systemd watcher."


def test_install_watcher_can_activate_plan_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
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

    rendered = render_docker_watcher_compose()

    assert f"{key_path}:{key_path}:ro" in rendered
    assert f"{known_hosts}:/root/.ssh/known_hosts:ro" in rendered
    assert f"{user_home / '.ssh'}:" not in rendered


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


def test_deploy_remote_local_sync_dry_run_includes_sync_and_compose(
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
    runner = CliRunner()

    result = runner.invoke(app, ["deploy", "--context", "prod", "--dry-run"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "mkdir -p /opt/sftpwarden" in output
    assert "rsync" in output
    assert "docker compose -f docker-compose.yml pull" in output
    assert "docker compose -f docker-compose.yml up -d --build" in output


def test_deploy_remote_only_dry_run_validates_remote_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    entry = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="archive", contexts={"archive": entry}))
    runner = CliRunner()

    result = runner.invoke(app, ["deploy", "--context", "archive", "--dry-run"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "test -f /opt/sftpwarden/sftpwarden.yaml" in output
    assert "rsync" not in output
    assert "docker compose -f docker-compose.yml up -d --build" in output


def test_deploy_context_accepts_injected_runner(tmp_path: Path) -> None:
    root = tmp_path / "dev-project"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = local_context("dev", root, config.provider.type)
    calls: list[tuple[list[str], str | None]] = []

    def fake_runner(command: list[str], *, cwd: str | None = None) -> None:
        calls.append((command, cwd))

    result = deploy_context(entry, runner=fake_runner)

    assert result == "Deployed dev."
    assert calls[0][0] == ["docker", "compose", "version"]
    assert calls[1][0] == ["docker", "compose", "-f", "docker-compose.yml", "pull"]
    assert calls[2][0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "up",
        "-d",
        "--build",
    ]
    assert {cwd for _, cwd in calls} == {str(root)}


def test_deploy_context_reports_missing_local_docker_compose(tmp_path: Path) -> None:
    root = tmp_path / "dev-project"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = local_context("dev", root, config.provider.type)

    def fake_runner(command: list[str], *, cwd: str | None = None) -> None:
        if command == ["docker", "compose", "version"]:
            raise SFTPWardenRuntimeError(
                "Deploy command failed.",
                suggestion="docker: compose is not a docker command",
            )

    with pytest.raises(SFTPWardenRuntimeError, match="Docker Compose is not available"):
        deploy_context(entry, runner=fake_runner)


def test_deploy_plan_and_required_files_error_edges(tmp_path: Path) -> None:
    malformed_remote = local_context("broken", tmp_path / "broken", ProviderType.YAML)
    malformed_remote.type = ContextType.REMOTE
    malformed_remote.remote = None

    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_module.deploy_plan(malformed_remote)
    with pytest.raises(ContextError, match="missing local settings"):
        deploy_module._local_deploy_plan(
            local_context("local", tmp_path / "missing", ProviderType.YAML).model_copy(
                update={"root": "", "config": ""}
            )
        )
    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_module._remote_local_sync_deploy_plan(malformed_remote)
    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_module._remote_only_deploy_plan(malformed_remote)
    remote_missing_local = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    remote_missing_local.root = ""
    remote_missing_local.config = ""
    with pytest.raises(ContextError, match="missing local settings"):
        deploy_module._remote_local_sync_deploy_plan(remote_missing_local)

    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev")
    config_path = root / "sftpwarden.yaml"
    compose_path = root / "docker-compose.yml"
    write_config(config_path, config)
    compose_path.write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(SFTPWardenRuntimeError, match="Required deploy file"):
        deploy_module.required_sync_files(root, config_path=config_path, compose_path=compose_path)

    users_path = root / "users.yaml"
    users_path.write_text(empty_provider_text(ProviderType.YAML), encoding="utf-8")
    excluded = root / ".env"
    excluded.write_text("SECRET=1\n", encoding="utf-8")
    with pytest.raises(SFTPWardenRuntimeError, match="Refusing to sync"):
        deploy_module.required_sync_files(root, config_path=config_path, compose_path=excluded)


def test_deploy_remote_runs_verification_and_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
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
    verified: list[str] = []
    commands: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        deploy_module,
        "verify_remote_runtime_requirements",
        lambda remote: verified.append(remote.host),
    )

    message = deploy_context(
        entry,
        runner=lambda command, *, cwd=None: commands.append((command, cwd)),
    )

    assert message == "Deployed prod."
    assert verified == ["example.com"]
    assert commands
    assert {cwd for _command, cwd in commands} == {None}


def test_deploy_context_rechecks_remote_settings_after_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed_remote = local_context("broken", tmp_path / "broken", ProviderType.YAML)
    malformed_remote.type = ContextType.REMOTE
    malformed_remote.remote = None
    monkeypatch.setattr(
        deploy_module,
        "deploy_plan",
        lambda _context: deploy_module.DeployPlan(commands=[]),
    )

    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_context(malformed_remote)


def test_run_command_delegates_to_checked_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        deploy_module,
        "run_checked",
        lambda command, **kwargs: calls.append((command, kwargs.get("cwd"))),
    )

    cwd = str(tmp_path / "project")
    deploy_module.run_command(["docker", "compose", "ps"], cwd=cwd)

    assert calls == [(["docker", "compose", "ps"], cwd)]


def test_refresh_context_executes_local_and_remote_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = local_context("dev", tmp_path / "dev", ProviderType.YAML)
    remote = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    calls: list[tuple[list[str], str | None]] = []

    class Result:
        stdout = ""

    def fake_run_checked(command: list[str], **kwargs: object) -> Result:
        calls.append((command, kwargs.get("cwd")))  # type: ignore[arg-type]
        return Result()

    monkeypatch.setattr(refresh_module, "run_checked", fake_run_checked)

    assert refresh_context(local) == "Refreshed dev."
    assert refresh_context(remote) == "Refreshed prod."
    assert calls[0][1] == str(tmp_path / "dev")
    assert calls[1][0][0] == "ssh"


def test_refresh_context_rejects_remote_without_settings(tmp_path: Path) -> None:
    malformed_remote = local_context("broken", tmp_path / "broken", ProviderType.YAML)
    malformed_remote.type = ContextType.REMOTE
    malformed_remote.remote = None

    with pytest.raises(ContextError, match="missing remote settings"):
        refresh_context(malformed_remote)


def test_sync_target_escapes_ssh_key_transport(tmp_path: Path) -> None:
    import shlex

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


def test_docker_watcher_ignores_context_without_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = local_context("dev", tmp_path / "dev", ProviderType.YAML)
    entry.type = ContextType.REMOTE
    entry.storage = "local-sync"  # type: ignore
    entry.remote = None
    monkeypatch.setattr(watcher_module, "docker_watcher_remote_contexts", lambda: [entry])

    assert watcher_module.docker_watcher_ssh_volumes() == []
