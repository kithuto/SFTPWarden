from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.watcher.core as watcher_module
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
from sftpwarden.refresh.core import refresh_context, resolve_refresh_targets
from sftpwarden.utils.errors import ContextError
from sftpwarden.watcher.core import (
    WatcherInstallMode,
    derive_watch_targets,
    remote_root_path,
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
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
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
    (tmp_path / "dev-project").mkdir()
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
    monkeypatch.setattr(
        watcher_module,
        "detect_native_watcher_mode",
        lambda: WatcherInstallMode.SYSTEMD,
    )
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
