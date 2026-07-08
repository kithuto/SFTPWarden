from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import sftpwarden.services.context_cleanup as context_cleanup
from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.config.global_config import load_global_config, save_global_config
from sftpwarden.contexts import (
    ContextRegistry,
    load_registry,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.render.compose import write_compose
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils.errors import ContextError, RuntimeError


@dataclass
class RecordingRunner:
    responses: dict[tuple[str, ...], CommandResult]

    def __post_init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], *, cwd: str | None = None) -> CommandResult:
        self.calls.append((args, cwd))
        for prefix, response in self.responses.items():
            if tuple(args[: len(prefix)]) == prefix:
                return response
        return CommandResult(args=args, returncode=0, stdout="", stderr="")


def _docker_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_cleanup.shutil,
        "which",
        lambda name: "docker" if name == "docker" else None,
    )


def _docker_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_cleanup.shutil, "which", lambda _name: None)


def test_prune_missing_local_context_removes_registry_and_orphaned_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    _docker_available(monkeypatch)
    missing_root = tmp_path / "deleted-project"
    entry = local_context("dev", missing_root, ProviderType.YAML)
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))
    runner = RecordingRunner(
        {
            ("docker", "ps", "-aq"): CommandResult(["docker"], 0, "container-1\n", ""),
            ("docker", "network", "ls"): CommandResult(["docker"], 0, "network-1\n", ""),
            ("docker", "volume", "ls"): CommandResult(["docker"], 0, "volume-1\n", ""),
        }
    )

    report = context_cleanup.prune_missing_contexts(runner=runner)

    assert report.removed_contexts == ["dev"]
    assert load_registry().contexts == {}
    assert load_registry().default is None
    assert any("containers" in message for message in report.local_runtime_messages)
    assert any(call[0][:3] == ["docker", "rm", "-f"] for call in runner.calls)
    assert any(call[0][:3] == ["docker", "network", "rm"] for call in runner.calls)
    assert any(call[0][:3] == ["docker", "volume", "rm"] for call in runner.calls)


def test_prune_missing_contexts_keeps_existing_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    entry = local_context("dev", project, ProviderType.YAML)
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))

    report = context_cleanup.prune_missing_contexts(
        runner=lambda *_args, **_kwargs: pytest.fail("runner should not be used")
    )

    assert report.removed_contexts == []
    assert "dev" in report.registry.contexts


def test_manual_remote_local_sync_deletion_is_local_only_and_uninstalls_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    missing_root = tmp_path / "deleted-local-sync"
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=missing_root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    config = load_global_config()
    config.watcher.installed = True
    config.watcher.mode = "systemd"
    config.watcher.activated = False
    save_global_config(config)

    report = context_cleanup.prune_missing_contexts(
        runner=lambda *_args, **_kwargs: pytest.fail("remote cleanup should not run")
    )

    assert report.removed_contexts == ["prod"]
    assert report.local_runtime_messages == []
    assert report.watcher_message == "Watcher uninstalled."
    assert not load_global_config().watcher.installed


def test_remove_local_context_cleans_compose_runtime_and_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    _docker_available(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    config = default_project_config("dev")
    write_config(project / "sftpwarden.yaml", config)
    write_compose(config, project)
    entry = local_context("dev", project, ProviderType.YAML)
    save_registry(ContextRegistry(default="dev", contexts={"dev": entry}))
    runner = RecordingRunner({})

    report = context_cleanup.remove_context_with_cleanup("dev", runner=runner)

    assert not project.exists()
    assert report.removed_local_root == project
    assert report.local_runtime_messages == ["Stopped Docker Compose runtime for dev."]
    assert runner.calls == [
        (["docker", "compose", "-f", "docker-compose.yml", "down"], str(project))
    ]
    assert load_registry().contexts == {}


def test_remove_shared_root_only_removes_selected_registry_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "shared"
    project.mkdir()
    write_config(project / "sftpwarden.yaml", default_project_config("dev"))
    dev = local_context("dev", project, ProviderType.YAML)
    qa = local_context("qa", project, ProviderType.YAML)
    save_registry(ContextRegistry(default="qa", contexts={"dev": dev, "qa": qa}))

    report = context_cleanup.remove_context_with_cleanup(
        "qa",
        runner=lambda *_args, **_kwargs: pytest.fail("shared root should not be cleaned"),
    )

    assert project.exists()
    assert report.removed_local_root is None
    assert report.registry.default == "dev"
    assert set(report.registry.contexts) == {"dev"}


def test_remove_remote_context_keeps_remote_by_default_and_removes_local_sync_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "prod"
    project.mkdir()
    write_config(project / "sftpwarden.yaml", default_project_config("prod"))
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))

    report = context_cleanup.remove_context_with_cleanup(
        "prod",
        runner=lambda *_args, **_kwargs: pytest.fail("SSH should not run without delete_remote"),
    )

    assert not project.exists()
    assert report.remote_messages == []
    assert load_registry().contexts == {}


def test_remove_remote_context_can_delete_remote_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "prod"
    project.mkdir()
    write_config(project / "sftpwarden.yaml", default_project_config("prod"))
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    runner = RecordingRunner({})

    report = context_cleanup.remove_context_with_cleanup(
        "prod",
        delete_remote=True,
        runner=runner,
    )

    assert not project.exists()
    assert report.remote_messages == [
        "Removed remote project for prod: deploy@example.com:/opt/sftpwarden"
    ]
    assert runner.calls[0][0][:5] == [
        "ssh",
        "-p",
        "22",
        "-o",
        "BatchMode=yes",
    ]
    assert "docker compose -f docker-compose.yml down" in runner.calls[0][0][-1]
    assert "rm -rf -- /opt/sftpwarden" in runner.calls[0][0][-1]


def test_remote_cleanup_rejects_missing_failed_or_unsafe_remote_settings(
    tmp_path: Path,
) -> None:
    local = local_context("dev", tmp_path / "dev", ProviderType.YAML)
    remote = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path / "prod",
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    failed = RecordingRunner({("ssh",): CommandResult(["ssh"], 1, "", "permission denied")})

    with pytest.raises(ContextError, match="no remote settings"):
        context_cleanup.cleanup_remote_project(local)
    with pytest.raises(RuntimeError, match="Remote cleanup failed"):
        context_cleanup.cleanup_remote_project(remote, runner=failed)

    assert remote.remote is not None
    unsafe = remote.model_copy(
        update={"remote": remote.remote.model_copy(update={"remote_root": "/"})}
    )
    with pytest.raises(ContextError, match="unsafe remote root"):
        context_cleanup.cleanup_remote_project(unsafe)


def test_cleanup_local_runtime_handles_missing_docker_compose_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = default_project_config("dev")
    write_config(project / "sftpwarden.yaml", config)
    entry = local_context("dev", project, ProviderType.YAML)

    assert (
        context_cleanup.cleanup_local_runtime(
            remote_context(
                name="prod",
                provider=ProviderType.YAML,
                remote_url="deploy@example.com:/opt/sftpwarden",
                local_root=project,
                remote_root="~/sftpwarden",
                remote_only=False,
                ssh_key=None,
                critical=True,
            )
        )
        == []
    )
    empty_root = entry.model_copy(update={"root": ""})
    assert context_cleanup.cleanup_local_runtime(empty_root) == []

    _docker_missing(monkeypatch)
    assert context_cleanup.cleanup_local_runtime(entry) == [
        "Skipped Docker cleanup for dev: docker was not found."
    ]

    _docker_available(monkeypatch)
    missing_config = entry.model_copy(update={"config": str(project / "missing.yaml")})
    missing_config_runner = RecordingRunner(
        {("docker", "ps", "-aq"): CommandResult(["docker"], 0, "container-1\n", "")}
    )
    assert context_cleanup.cleanup_local_runtime(
        missing_config,
        runner=missing_config_runner,
    ) == ["Removed Docker containers for missing context dev."]

    missing_compose_runner = RecordingRunner(
        {("docker", "ps", "-aq"): CommandResult(["docker"], 0, "container-1\n", "")}
    )
    assert context_cleanup.cleanup_local_runtime(
        entry,
        runner=missing_compose_runner,
    ) == ["Removed Docker containers for missing context dev."]

    write_compose(config, project)
    failed_down = RecordingRunner(
        {
            ("docker", "compose", "-f", "docker-compose.yml", "down"): CommandResult(
                ["docker"], 1, "", "daemon unavailable"
            )
        }
    )
    assert context_cleanup.cleanup_local_runtime(entry, runner=failed_down) == [
        "Skipped Docker cleanup for dev: daemon unavailable"
    ]


def test_cleanup_orphaned_runtime_reports_docker_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _docker_missing(monkeypatch)
    entry = local_context("dev", tmp_path / "missing", ProviderType.YAML)
    assert context_cleanup.cleanup_orphaned_local_runtime(entry) == []

    _docker_available(monkeypatch)
    runner = RecordingRunner(
        {
            ("docker", "ps", "-aq"): CommandResult(["docker"], 0, "container-1\n", ""),
            ("docker", "rm", "-f"): CommandResult(["docker"], 1, "", "rm failed"),
            ("docker", "network", "ls"): CommandResult(["docker"], 0, "network-1\n", ""),
            ("docker", "network", "rm"): CommandResult(["docker"], 1, "", "network failed"),
            ("docker", "volume", "ls"): CommandResult(["docker"], 0, "volume-1\n", ""),
            ("docker", "volume", "rm"): CommandResult(["docker"], 1, "", "volume failed"),
        }
    )

    messages = context_cleanup.cleanup_orphaned_local_runtime(entry, runner=runner)

    assert messages == [
        "Skipped Docker container cleanup for dev: rm failed",
        "Skipped Docker network cleanup for dev: network failed",
        "Skipped Docker volume cleanup for dev: volume failed",
    ]

    no_ids = RecordingRunner(
        {("docker", "ps", "-aq"): CommandResult(["docker"], 1, "", "daemon down")}
    )
    assert context_cleanup.cleanup_orphaned_local_runtime(entry, runner=no_ids) == []


def test_remove_local_root_requires_matching_project_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    entry = local_context("dev", project, ProviderType.YAML)

    empty_entry = entry.model_copy(update={"root": "", "config": ""})
    assert context_cleanup.remove_local_project_root(empty_entry) is None
    missing_root_entry = local_context("dev", tmp_path / "missing", ProviderType.YAML)
    assert context_cleanup.remove_local_project_root(missing_root_entry) is None

    assert context_cleanup.remove_local_project_root(entry) is None

    write_config(project / "sftpwarden.yaml", default_project_config("other"))
    assert context_cleanup.remove_local_project_root(entry) is None

    external_config = tmp_path / "external.yaml"
    write_config(external_config, default_project_config("dev"))
    external_config_entry = entry.model_copy(update={"config": str(external_config)})
    assert context_cleanup.remove_local_project_root(external_config_entry) is None

    invalid_config = project / "invalid.yaml"
    invalid_config.write_text("project: [\n", encoding="utf-8")
    invalid_config_entry = entry.model_copy(update={"config": str(invalid_config)})
    assert context_cleanup.remove_local_project_root(invalid_config_entry) is None

    write_config(project / "sftpwarden.yaml", default_project_config("dev"))
    fallback_config_entry = entry.model_copy(update={"config": ""})
    assert context_cleanup.remove_local_project_root(fallback_config_entry) == project
    assert not project.exists()


def test_cleanup_watcher_refreshes_docker_metadata_when_targets_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path / "prod",
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    registry = ContextRegistry(default="prod", contexts={"prod": entry})
    config = load_global_config()
    config.watcher.installed = True
    config.watcher.mode = "docker"
    config.watcher.activated = True
    save_global_config(config)
    calls: list[tuple[str, Any]] = []

    class Plan:
        commands = [["docker", "compose", "up", "-d"]]

    monkeypatch.setattr("sftpwarden.watcher.watcher_install_plan", lambda _mode: Plan())
    monkeypatch.setattr(
        "sftpwarden.watcher.write_watcher_files",
        lambda plan: calls.append(("write", plan)),
    )
    monkeypatch.setattr(
        "sftpwarden.watcher.run_watcher_commands",
        lambda commands: calls.append(("run", commands)),
    )

    message = context_cleanup.cleanup_watcher_if_unused(registry)

    assert message == "Updated Docker watcher context metadata."
    assert calls[0][0] == "write"
    assert isinstance(calls[0][1], Plan)
    assert calls[1] == ("run", [["docker", "compose", "up", "-d"]])


def test_cleanup_watcher_keeps_native_watcher_when_targets_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path / "prod",
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    config = load_global_config()
    config.watcher.installed = True
    config.watcher.mode = "systemd"
    save_global_config(config)

    message = context_cleanup.cleanup_watcher_if_unused(
        ContextRegistry(default="prod", contexts={"prod": entry})
    )

    assert message is None
    assert load_global_config().watcher.installed


def test_remove_remote_only_context_has_no_local_root_cleanup(
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

    report = context_cleanup.remove_context_with_cleanup(
        "archive",
        runner=lambda *_args, **_kwargs: pytest.fail("remote-only local cleanup should not run"),
    )

    assert report.removed_local_root is None
    assert report.local_runtime_messages == []
    assert load_registry().contexts == {}


def test_remote_only_root_check_removes_stale_context_when_remote_root_is_missing(
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
    runner = RecordingRunner(
        {
            ("ssh",): CommandResult(
                ["ssh"],
                context_cleanup.REMOTE_ROOT_MISSING_EXIT_CODE,
                "",
                "",
            )
        }
    )

    with pytest.raises(ContextError, match="no longer exists"):
        context_cleanup.ensure_remote_only_root_available(entry, runner=runner)

    assert load_registry().contexts == {}
    assert load_registry().default is None


def test_remote_only_root_check_reports_unresponsive_remote_server(
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
    runner = RecordingRunner({("ssh",): CommandResult(["ssh"], 255, "", "timed out")})

    with pytest.raises(ContextError, match="Remote server for context archive is not responding"):
        context_cleanup.ensure_remote_only_root_available(entry, runner=runner)

    assert "archive" in load_registry().contexts


def test_remote_only_root_check_rejects_unsafe_remote_root(tmp_path: Path) -> None:
    entry = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/",
        local_root=None,
        remote_root="/",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )

    with pytest.raises(ContextError, match="unsafe remote root"):
        context_cleanup.ensure_remote_only_root_available(
            entry,
            runner=lambda *_args, **_kwargs: pytest.fail("unsafe root should not use SSH"),
        )


def test_remote_only_root_check_requires_remote_settings_and_stale_remove_is_idempotent(
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
    broken = entry.model_copy(update={"remote": None})
    save_registry(ContextRegistry())

    with pytest.raises(ContextError, match="missing remote settings"):
        context_cleanup.ensure_remote_only_root_available(broken)

    registry = context_cleanup.remove_stale_context_entry("archive")
    assert registry.contexts == {}


def test_remote_only_root_check_accepts_existing_root_and_skips_other_contexts(
    tmp_path: Path,
) -> None:
    remote_only = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    local = local_context("dev", tmp_path / "dev", ProviderType.YAML)
    runner = RecordingRunner({("ssh",): CommandResult(["ssh"], 0, "", "")})

    context_cleanup.ensure_remote_only_root_available(remote_only, runner=runner)
    context_cleanup.ensure_remote_only_root_available(
        local,
        runner=lambda *_args, **_kwargs: pytest.fail("local context should not use SSH"),
    )

    assert runner.calls[0][0][0] == "ssh"


def test_context_cleanup_reports_unknown_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    save_registry(ContextRegistry())

    with pytest.raises(ContextError, match="Unknown context"):
        context_cleanup.remove_context_with_cleanup("missing")
