from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from sftpwarden.config import RemoteStorage
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    ContextType,
    load_registry,
    save_registry,
)
from sftpwarden.remote.deploy import remote_shell_command
from sftpwarden.system.commands import CommandResult, command_text, run
from sftpwarden.utils.constants import CONFIG_FILENAME
from sftpwarden.utils.errors import ContextError, RuntimeError, SFTPWardenError
from sftpwarden.utils.paths import expand_path

COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
REMOTE_ROOT_MISSING_EXIT_CODE = 42


class CleanupRunner(Protocol):
    """Callable used to run cleanup commands."""

    def __call__(self, args: list[str], *, cwd: str | None = None) -> CommandResult:
        """Execute one cleanup command and return its result."""
        ...


@dataclass(frozen=True)
class ContextCleanupReport:
    """Result of removing one registered context."""

    name: str
    registry: ContextRegistry
    removed_local_root: Path | None = None
    local_runtime_messages: list[str] = field(default_factory=list)
    remote_messages: list[str] = field(default_factory=list)
    watcher_message: str | None = None


@dataclass(frozen=True)
class MissingContextCleanupReport:
    """Result of pruning contexts whose local root disappeared."""

    registry: ContextRegistry
    removed_contexts: list[str] = field(default_factory=list)
    local_runtime_messages: list[str] = field(default_factory=list)
    watcher_message: str | None = None


def prune_missing_contexts(*, runner: CleanupRunner = run) -> MissingContextCleanupReport:
    """Remove registry entries whose entire local context folder disappeared.

    Manual pruning is intentionally local-only for remote contexts: it removes
    stale local registry/watcher state but never SSHes into the remote host.
    """
    registry = load_registry()
    missing = [
        (name, entry) for name, entry in registry.contexts.items() if _context_root_missing(entry)
    ]
    if not missing:
        return MissingContextCleanupReport(registry=registry)

    local_runtime_messages: list[str] = []
    for name, entry in missing:
        if entry.type == ContextType.LOCAL:
            local_runtime_messages.extend(cleanup_orphaned_local_runtime(entry, runner=runner))
        del registry.contexts[name]

    _repair_default(registry)
    save_registry(registry)
    watcher_message = cleanup_watcher_if_unused(registry)
    return MissingContextCleanupReport(
        registry=registry,
        removed_contexts=[name for name, _entry in missing],
        local_runtime_messages=local_runtime_messages,
        watcher_message=watcher_message,
    )


def remove_context_with_cleanup(
    name: str,
    *,
    delete_remote: bool = False,
    runner: CleanupRunner = run,
) -> ContextCleanupReport:
    """Remove a context and clean project-owned local and optional remote state."""
    registry = load_registry()
    try:
        entry = registry.contexts[name]
    except KeyError as exc:
        raise ContextError(
            f"Unknown context: {name}", suggestion="Run `sftpwarden context ls`."
        ) from exc

    local_root_shared = _local_root_shared(registry, name, entry)
    remote_messages = cleanup_remote_project(entry, runner=runner) if delete_remote else []
    local_runtime_messages: list[str] = []
    removed_local_root: Path | None = None
    if not local_root_shared:
        if entry.type == ContextType.LOCAL:
            local_runtime_messages.extend(cleanup_local_runtime(entry, runner=runner))
        removed_local_root = remove_local_project_root(entry)

    del registry.contexts[name]
    _repair_default(registry)
    save_registry(registry)
    watcher_message = cleanup_watcher_if_unused(registry)
    return ContextCleanupReport(
        name=name,
        registry=registry,
        removed_local_root=removed_local_root,
        local_runtime_messages=local_runtime_messages,
        remote_messages=remote_messages,
        watcher_message=watcher_message,
    )


def ensure_remote_only_root_available(
    entry: ContextEntry,
    *,
    runner: CleanupRunner = run,
) -> None:
    """Validate the remote root for an operation on a remote-only context.

    If the remote root was deleted manually, the stale local registry entry is
    removed. If SSH cannot establish a usable connection, the caller receives a
    controlled context error instead of raw command output.
    """
    if entry.type != ContextType.REMOTE or entry.storage != RemoteStorage.REMOTE_ONLY:
        return
    if not entry.remote:
        raise ContextError(f"Remote context {entry.name} is missing remote settings.")

    remote_root = entry.remote.remote_root.rstrip("/")
    if not remote_root:
        raise ContextError(
            f"Refusing to validate unsafe remote root for context {entry.name}: "
            f"{entry.remote.remote_root!r}"
        )
    command = (
        f"if [ -d {shlex.quote(remote_root)} ]; then "
        "exit 0; "
        f"else exit {REMOTE_ROOT_MISSING_EXIT_CODE}; fi"
    )
    result = runner(remote_shell_command(entry.remote, command))
    if result.returncode == 0:
        return
    if result.returncode == REMOTE_ROOT_MISSING_EXIT_CODE:
        remove_stale_context_entry(entry.name)
        raise ContextError(
            f"Remote project root for context {entry.name} no longer exists.",
            suggestion=(
                "The stale context was removed from the local registry. "
                "Recreate or re-register the remote-only context if it is still needed."
            ),
        )
    endpoint = f"{entry.remote.user}@{entry.remote.host}:{entry.remote.port}"
    raise ContextError(
        f"Remote server for context {entry.name} is not responding.",
        suggestion=(
            f"Check SSH connectivity to {endpoint}. "
            "If the context is no longer needed, remove it with "
            f"`sftpwarden context remove {entry.name} --yes`."
        ),
    )


def remove_stale_context_entry(name: str) -> ContextRegistry:
    """Remove one stale context entry without touching local or remote project files."""
    registry = load_registry()
    if name not in registry.contexts:
        return registry
    del registry.contexts[name]
    _repair_default(registry)
    save_registry(registry)
    cleanup_watcher_if_unused(registry)
    return registry


def cleanup_local_runtime(
    entry: ContextEntry,
    *,
    runner: CleanupRunner = run,
) -> list[str]:
    """Best-effort cleanup for a local Docker Compose runtime."""
    if entry.type != ContextType.LOCAL or not entry.root:
        return []
    root = expand_path(entry.root)
    config_path = _entry_config_path(entry)
    if not config_path.exists():
        return cleanup_orphaned_local_runtime(entry, runner=runner)
    if shutil.which("docker") is None:
        return [f"Skipped Docker cleanup for {entry.name}: docker was not found."]
    compose_file = _compose_file_from_config(config_path)
    compose_path = root / compose_file
    if not compose_path.exists():
        return cleanup_orphaned_local_runtime(entry, runner=runner)
    result = runner(["docker", "compose", "-f", compose_file, "down"], cwd=str(root))
    if result.returncode != 0:
        return [
            f"Skipped Docker cleanup for {entry.name}: {result.output or command_text(result.args)}"
        ]
    return [f"Stopped Docker Compose runtime for {entry.name}."]


def cleanup_orphaned_local_runtime(
    entry: ContextEntry,
    *,
    runner: CleanupRunner = run,
) -> list[str]:
    """Remove Docker Compose resources labelled with a deleted project root."""
    if not entry.root or shutil.which("docker") is None:
        return []
    root = expand_path(entry.root).resolve(strict=False)
    label = f"{COMPOSE_WORKING_DIR_LABEL}={root}"
    messages: list[str] = []
    containers = _docker_ids(["docker", "ps", "-aq", "--filter", f"label={label}"], runner)
    if containers:
        result = runner(["docker", "rm", "-f", *containers])
        if result.returncode == 0:
            messages.append(f"Removed Docker containers for missing context {entry.name}.")
        else:
            messages.append(
                f"Skipped Docker container cleanup for {entry.name}: "
                f"{result.output or command_text(result.args)}"
            )
    networks = _docker_ids(["docker", "network", "ls", "-q", "--filter", f"label={label}"], runner)
    if networks:
        result = runner(["docker", "network", "rm", *networks])
        if result.returncode == 0:
            messages.append(f"Removed Docker networks for missing context {entry.name}.")
        else:
            messages.append(
                f"Skipped Docker network cleanup for {entry.name}: "
                f"{result.output or command_text(result.args)}"
            )
    volumes = _docker_ids(["docker", "volume", "ls", "-q", "--filter", f"label={label}"], runner)
    if volumes:
        result = runner(["docker", "volume", "rm", *volumes])
        if result.returncode == 0:
            messages.append(f"Removed Docker volumes for missing context {entry.name}.")
        else:
            messages.append(
                f"Skipped Docker volume cleanup for {entry.name}: "
                f"{result.output or command_text(result.args)}"
            )
    return messages


def cleanup_remote_project(
    entry: ContextEntry,
    *,
    runner: CleanupRunner = run,
) -> list[str]:
    """Remove remote Docker Compose runtime and project files for a remote context."""
    if not entry.remote:
        raise ContextError(
            f"Context {entry.name} has no remote settings.",
            suggestion="Use --delete-remote only with a remote context.",
        )
    remote = entry.remote
    remote_root = remote.remote_root.rstrip("/")
    if not remote_root:
        raise ContextError(
            f"Refusing to remove unsafe remote root for context {entry.name}: "
            f"{remote.remote_root!r}"
        )
    compose_path = f"{remote_root}/{remote.compose_file}"
    command = (
        f"if [ -f {shlex.quote(compose_path)} ]; then "
        f"cd {shlex.quote(remote_root)} && "
        f"docker compose -f {shlex.quote(remote.compose_file)} down; "
        f"fi && rm -rf -- {shlex.quote(remote_root)}"
    )
    ssh_command = remote_shell_command(remote, command)
    result = runner(ssh_command)
    if result.returncode != 0:
        raise RuntimeError(
            f"Remote cleanup failed for context {entry.name}.",
            suggestion=result.output
            or "Verify SSH access, Docker Compose, and remote permissions.",
        )
    return [f"Removed remote project for {entry.name}: {remote.user}@{remote.host}:{remote_root}"]


def remove_local_project_root(entry: ContextEntry) -> Path | None:
    """Delete a context root only when it is clearly the registered project root."""
    root = _safe_local_project_root(entry)
    if root is None:
        return None
    shutil.rmtree(root)
    return root


def cleanup_watcher_if_unused(registry: ContextRegistry) -> str | None:
    """Uninstall or refresh watcher files after registry changes."""
    from sftpwarden.config.global_config import load_global_config
    from sftpwarden.watcher import (
        run_watcher_commands,
        uninstall_watcher,
        watcher_install_plan,
        write_watcher_files,
    )
    from sftpwarden.watcher.base import WatcherInstallMode

    watcher = load_global_config().watcher
    if not watcher.installed:
        return None
    has_local_sync = any(
        entry.type == ContextType.REMOTE and entry.storage == RemoteStorage.LOCAL_SYNC
        for entry in registry.contexts.values()
    )
    if has_local_sync:
        if watcher.mode != WatcherInstallMode.DOCKER.value:
            return None
        plan = watcher_install_plan(WatcherInstallMode.DOCKER)
        write_watcher_files(plan)
        if watcher.activated is not False:
            run_watcher_commands(plan.commands)
        return "Updated Docker watcher context metadata."
    return uninstall_watcher()


def _context_root_missing(entry: ContextEntry) -> bool:
    """Return whether a configured context root no longer exists."""
    return bool(entry.root) and not expand_path(entry.root).exists()


def _repair_default(registry: ContextRegistry) -> None:
    """Select a valid default after registry entries are removed."""
    if registry.default is not None and registry.default not in registry.contexts:
        registry.default = next(iter(registry.contexts), None)


def _local_root_shared(registry: ContextRegistry, name: str, entry: ContextEntry) -> bool:
    """Return whether another context references the same local root."""
    if not entry.root:
        return False
    root = expand_path(entry.root).resolve(strict=False)
    for other_name, other in registry.contexts.items():
        if other_name == name or not other.root:
            continue
        if expand_path(other.root).resolve(strict=False) == root:
            return True
    return False


def _entry_config_path(entry: ContextEntry) -> Path:
    """Return the explicit or root-relative config path for a context."""
    if entry.config:
        return expand_path(entry.config)
    return expand_path(entry.root) / CONFIG_FILENAME


def _safe_local_project_root(entry: ContextEntry) -> Path | None:
    """Return a verified project root that is safe to delete."""
    if not entry.root:
        return None
    root = expand_path(entry.root)
    if not root.exists() or not root.is_dir():
        return None
    config_path = _entry_config_path(entry)
    if not config_path.exists():
        return None
    try:
        config_path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    from sftpwarden.config import load_config

    try:
        config = load_config(config_path)
    except (SFTPWardenError, yaml.YAMLError):
        return None
    if config.project.name != entry.name:
        return None
    return root


def _compose_file_from_config(config_path: Path) -> str:
    """Return the Compose filename declared by a project config."""
    from sftpwarden.config import load_config

    return load_config(config_path).docker.compose_file


def _docker_ids(command: list[str], runner: CleanupRunner) -> list[str]:
    """Return Docker object identifiers emitted by a successful command."""
    result = runner(command)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
