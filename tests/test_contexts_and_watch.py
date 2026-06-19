from __future__ import annotations

from pathlib import Path

import pytest

from sftpwarden.config import default_project_config, write_config
from sftpwarden.contexts import (
    ContextRegistry,
    ContextType,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.providers import empty_provider_text
from sftpwarden.refresh import refresh_context, resolve_refresh_targets
from sftpwarden.watcher import derive_watch_targets, should_watch


def test_remote_only_context_has_empty_top_level_paths() -> None:
    entry = remote_context(
        name="archive",
        provider="csv",
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
    one = local_context("one", tmp_path / "one", "yaml")
    two = local_context("two", tmp_path / "two", "yaml")
    save_registry(ContextRegistry(default="one", contexts={"one": one, "two": two}))

    targets = resolve_refresh_targets(all_contexts=True)

    assert [target.name for target in targets] == ["one", "two"]


def test_remote_default_ssh_key_uses_ssh_defaults() -> None:
    entry = remote_context(
        name="prod",
        provider="yaml",
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
