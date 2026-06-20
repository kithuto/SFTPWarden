from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

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
from sftpwarden.watcher import derive_watch_targets, render_docker_watcher_compose, sync_target


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
    assert entry.watcher_required is False
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


def test_watch_derives_only_config_and_provider_files(
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

    assert {target.local_path.name for target in targets} == {"sftpwarden.yaml", "users.yaml"}
    assert {target.remote_path for target in targets} == {
        "/opt/sftpwarden/sftpwarden.yaml",
        "/opt/sftpwarden/users.yaml",
    }


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
    assert data["dry_run"] is True
    assert {target["remote_path"] for target in data["targets"]} == {
        "/opt/sftpwarden/sftpwarden.yaml",
        "/opt/sftpwarden/users.yaml",
    }


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

    assert {target.local_path.name for target in targets} == {"sftpwarden.yaml", "accounts.csv"}
    assert {target.remote_path for target in targets} == {
        "/opt/sftpwarden/sftpwarden.yaml",
        "/opt/sftpwarden/accounts.csv",
    }


def test_watch_sql_provider_syncs_only_context_config(
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

    assert [(target.local_path.name, target.remote_path) for target in targets] == [
        ("sftpwarden.yaml", "/opt/sftpwarden/sftpwarden.yaml")
    ]


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
    assert data["dry_run"] is True
    assert data["targets"][0]["context"] == "dev"
    assert "docker compose" in data["targets"][0]["result"]


def test_refresh_all_requires_registered_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))

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
    assert load_global_config().watcher.installed is True
    assert load_global_config().watcher.mode == "systemd"


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
    assert load_global_config().watcher.installed is False


def test_watcher_install_is_idempotent_and_can_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()

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


def test_watcher_status_json_reports_state_and_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    runner.invoke(app, ["watcher", "install", "--watcher", "systemd", "--yes", "--no-activate"])

    result = runner.invoke(app, ["watcher", "status", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["installed"] is True
    assert data["mode"] == "systemd"
    assert data["targets"] == []


def test_watcher_uninstall_clears_global_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    runner.invoke(app, ["watcher", "install", "--watcher", "systemd", "--yes", "--no-activate"])

    result = runner.invoke(app, ["watcher", "uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    assert load_global_config().watcher.installed is False


def test_systemd_watcher_install_plan_uses_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()

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
    assert load_global_config().watcher.installed is True
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
    assert "docker compose -f docker-compose.yml up -d" in output


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
    assert "docker compose -f docker-compose.yml up -d" in output


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
    assert calls[0][0] == ["docker", "compose", "-f", "docker-compose.yml", "pull"]
    assert {cwd for _, cwd in calls} == {str(root)}


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
