from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sftpwarden.cli import app
from sftpwarden.config import ProviderType, default_project_config, write_config
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
from sftpwarden.utils.errors import ContextError
from sftpwarden.watcher import derive_watch_targets, should_watch


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
    assert not should_watch(project / "docker-compose.yml")
    assert not should_watch(project / "old" / "users.yaml")


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
    output = result.output.replace("\n", " ")

    assert result.exit_code == 0, result.output
    assert "mkdir -p  /opt/sftpwarden" in output
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
    output = result.output.replace("\n", " ")

    assert result.exit_code == 0, result.output
    assert "test -f  /opt/sftpwarden/sftpwarden.yaml" in output
    assert "rsync" not in output
    assert "docker compose -f docker-compose.yml up -d" in output
