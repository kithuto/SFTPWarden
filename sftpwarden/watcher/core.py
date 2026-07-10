from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sftpwarden.config import (
    WATCHER_SYNC_PROVIDER_TYPES,
    SFTPWardenConfig,
    load_config,
    provider_local_path,
)
from sftpwarden.config.global_config import load_global_config, save_global_config
from sftpwarden.contexts import ContextEntry, ContextType, load_registry
from sftpwarden.remote.ssh import (
    rsync_ssh_transport,
    scp_upload_command,
)
from sftpwarden.system.commands import command_text, run_checked
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.platform import system_is
from sftpwarden.watcher import backends as watcher_backends
from sftpwarden.watcher.backends.docker import DockerComposeMount
from sftpwarden.watcher.base import (
    BaseWatcher,
    WatcherImageReference,
    WatcherInstallMode,
    WatcherInstallPlan,
    WatcherUninstallPlan,
)
from sftpwarden.watcher.registry import (
    registered_watchers,
    watcher_class,
)


class WatcherDockerFallbackRequired(ContextError):
    """Raised when auto mode found no native scheduler and Docker needs consent."""


@dataclass(frozen=True)
class WatchTarget:
    """File that should be watched and synced to a remote host."""

    context: str
    local_path: Path
    remote_path: str


def watcher_image_reference(
    image: str | None = None, *, allow_local_build: bool = True
) -> WatcherImageReference:
    """Resolve the Docker watcher image for source checkouts and packaged installs."""
    return watcher_backends.watcher_image_reference(image, allow_local_build=allow_local_build)


def docker_watcher_remote_contexts() -> list[ContextEntry]:
    """Return remote local-sync contexts relevant to Docker watcher."""
    return watcher_backends.docker_watcher_remote_contexts()


def docker_watcher_ssh_volumes() -> list[DockerComposeMount]:
    """Return SSH-related Docker volume mounts for watcher contexts."""
    return watcher_backends.docker_watcher_ssh_volumes()


def render_docker_watcher_compose(*, image: str | None = None) -> str:
    """Render Docker Compose YAML for the watcher."""
    return watcher_backends.render_docker_watcher_compose(image=image)


def docker_watcher_compose_path() -> Path:
    """Return the generated Docker watcher compose path."""
    return watcher_backends.docker_watcher_compose_path()


def remote_root_path(context: ContextEntry, local_path: Path) -> str:
    """Map a local project file to its remote path."""
    if not context.remote:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    if not context.root:
        raise ContextError(f"Context {context.name} is missing local root.")
    relative_path = local_path.resolve().relative_to(Path(context.root).resolve())
    return f"{context.remote.remote_root.rstrip('/')}/{relative_path.as_posix()}"


def editable_sync_target(context: ContextEntry, config: SFTPWardenConfig) -> WatchTarget | None:
    """Return the user-provider file that should be synced for a context."""
    if not context.remote or config.provider.type not in WATCHER_SYNC_PROVIDER_TYPES:
        return None
    provider_path = provider_local_path(context.root, config)
    if not provider_path.exists():
        return None
    return WatchTarget(
        context=context.name,
        local_path=provider_path,
        remote_path=remote_root_path(context, provider_path),
    )


def derive_watch_targets() -> list[WatchTarget]:
    """Derive watch targets from the context registry."""
    registry = load_registry()
    targets: list[WatchTarget] = []
    for context in registry.contexts.values():
        if (
            context.type != ContextType.REMOTE
            or context.storage != "local-sync"
            or not context.remote
        ):
            continue
        if not context.root or not context.config:
            continue
        config_path = Path(context.config)
        if not config_path.exists():
            continue
        config = load_config(config_path)
        target = editable_sync_target(context, config)
        if target:
            targets.append(target)
    return sorted(targets, key=lambda target: (target.context, str(target.local_path)))


def watcher_status_text() -> str:
    """Return watcher status as human-readable text."""
    data = watcher_status_data()
    lines = [
        f"Watcher installed: {data['installed']}",
        f"Watcher mode: {data['mode'] or ''}",
        f"Watcher path: {data['path'] or ''}",
        f"Remote local-sync targets: {len(data['targets'])}",
    ]
    lines.extend(f"- {target['context']}: {target['local_path']}" for target in data["targets"])
    return "\n".join(lines)


def watcher_status_data() -> dict[str, Any]:
    """Return watcher status as structured data."""
    state = load_global_config().watcher
    targets = derive_watch_targets()
    return {
        "installed": state.installed,
        "mode": state.mode,
        "path": state.path,
        "activated": state.activated,
        "targets": [
            {
                "context": target.context,
                "local_path": str(target.local_path),
                "remote_path": target.remote_path,
            }
            for target in targets
        ],
    }


def default_watcher_mode() -> WatcherInstallMode:
    """Return the configured default watcher mode."""
    mode = load_global_config().defaults.watcher_mode
    return WatcherInstallMode(mode)


def installed_watcher_mode() -> WatcherInstallMode | None:
    """Return the installed watcher mode."""
    state = load_global_config().watcher
    if not state.installed or not state.mode:
        return None
    return WatcherInstallMode(state.mode)


def native_watcher_classes() -> list[type[BaseWatcher]]:
    """Return native watcher backends in auto-detection order."""
    return sorted(
        (
            watcher
            for watcher in registered_watchers().values()
            if watcher.mode != WatcherInstallMode.AUTO and watcher.native_scheduler
        ),
        key=lambda watcher: watcher.auto_priority,
    )


def detect_native_watcher_mode() -> WatcherInstallMode | None:
    """Detect the best native watcher backend for the current host."""
    for watcher in native_watcher_classes():
        if watcher.is_supported():
            return watcher.mode
    return None


def docker_fallback_error() -> WatcherDockerFallbackRequired:
    """Build the standard Docker fallback prompt/error."""
    return WatcherDockerFallbackRequired(
        "No supported native watcher scheduler was detected.",
        suggestion=(
            "Run `sftpwarden watcher install --watcher docker` to use the Docker watcher, "
            "or install a native scheduler such as systemd, OpenRC, runit, supervisord, "
            "launchd, or Windows Task Scheduler."
        ),
    )


def resolve_watcher_mode(
    mode: str | WatcherInstallMode | None = None,
    *,
    allow_docker_fallback: bool = False,
) -> WatcherInstallMode:
    """Resolve an explicit or default watcher mode."""
    requested = WatcherInstallMode(mode or default_watcher_mode())
    if requested != WatcherInstallMode.AUTO:
        return requested
    detected = detect_native_watcher_mode()
    if detected is not None:
        return detected
    if allow_docker_fallback:
        return WatcherInstallMode.DOCKER
    raise docker_fallback_error()


def install_watcher(
    *,
    mode: str | WatcherInstallMode | None = None,
    yes: bool = False,
    dry_run: bool = False,
    image: str | None = None,
    activate: bool = False,
    allow_docker_fallback: bool = False,
) -> str:
    """Install watcher files and optionally activate them."""
    selected_mode = resolve_watcher_mode(
        mode,
        allow_docker_fallback=allow_docker_fallback,
    )
    backend = watcher_class(selected_mode)
    if image and not backend.accepts_image:
        raise ContextError("watcher --image is only valid when the resolved mode is docker.")

    config = load_global_config()
    existing = config.watcher
    replacing = existing.installed and existing.mode != selected_mode.value
    if existing.installed and existing.mode == selected_mode.value:
        return f"Watcher already installed in {selected_mode.value} mode."
    if replacing and not yes:
        raise ContextError(
            f"Watcher is already installed in {existing.mode} mode.",
            suggestion="Re-run with --yes to replace it.",
        )

    plan = watcher_install_plan(selected_mode, image=image)
    uninstall_plan = (
        watcher_uninstall_plan(
            WatcherInstallMode(existing.mode),
            path=Path(existing.path) if existing.path else None,
        )
        if replacing and existing.mode
        else None
    )
    if dry_run:
        if uninstall_plan:
            return "\n".join(
                [
                    f"Would replace existing {existing.mode} watcher.",
                    uninstall_plan.text(),
                    plan.text(),
                ]
            )
        return plan.text()
    if activate and not backend.is_supported():
        raise ContextError(
            f"{selected_mode.value} watcher is not supported on this host.",
            suggestion=(
                "Use `sftpwarden watcher install --watcher auto`, choose a scheduler "
                "available on this system, or use `--no-activate` to only render files."
            ),
        )
    if uninstall_plan:
        if existing.activated is not False:
            run_watcher_commands(uninstall_plan.commands)
        if uninstall_plan.path and uninstall_plan.path.exists():
            uninstall_plan.path.unlink()
    write_watcher_files(plan, image=image)
    if activate:
        run_watcher_commands(plan.commands)
    config.watcher.installed = True
    config.watcher.mode = selected_mode.value
    config.watcher.path = str(plan.path)
    config.watcher.activated = activate
    save_global_config(config)
    return f"Installed {selected_mode.value} watcher at {plan.path}."


def run_watcher_commands(commands: list[list[str]]) -> None:
    """Run watcher activation commands."""
    for command in commands:
        run_checked(
            command,
            error_type=ContextError,
            message=f"Watcher command failed: {' '.join(command)}",
            fallback_suggestion=(
                "Check scheduler permissions and systemd, OpenRC, runit, supervisord, "
                "launchd, Windows Task Scheduler, or Docker availability."
            ),
            capture_output=False,
        )


def uninstall_watcher(*, dry_run: bool = False, deactivate: bool | None = None) -> str:
    """Uninstall watcher metadata and generated files."""
    config = load_global_config()
    state = config.watcher
    if not state.installed:
        return "Watcher is not installed."
    path = Path(state.path) if state.path else None
    if not state.mode:
        raise ContextError("Installed watcher metadata is missing its mode.")
    plan = watcher_uninstall_plan(WatcherInstallMode(state.mode), path=path)
    if dry_run:
        return f"Would uninstall {state.mode} watcher at {path or ''}.\n{plan.text()}"
    should_deactivate = state.activated is not False if deactivate is None else deactivate
    if should_deactivate:
        run_watcher_commands(plan.commands)
    if path and path.exists():
        path.unlink()
    config.watcher.installed = False
    config.watcher.mode = None
    config.watcher.path = None
    config.watcher.activated = None
    save_global_config(config)
    return "Watcher uninstalled."


def ensure_watcher(
    *,
    requested_mode: str | None = None,
    yes: bool = False,
    image: str | None = None,
    allow_docker_fallback: bool = False,
) -> str:
    """Ensure a watcher is installed."""
    config = load_global_config()
    state = config.watcher
    if state.installed and not requested_mode:
        return f"Using existing {state.mode} watcher."
    selected_mode = requested_mode or config.defaults.watcher_mode
    return install_watcher(
        mode=selected_mode,
        yes=yes,
        image=image,
        allow_docker_fallback=allow_docker_fallback,
    )


def watcher_install_plan(
    mode: WatcherInstallMode | str,
    *,
    image: str | None = None,
) -> WatcherInstallPlan:
    """Build a watcher install plan."""
    selected_mode = resolve_watcher_mode(mode)
    backend = watcher_class(selected_mode)
    if image and not backend.accepts_image:
        raise ContextError("watcher --image is only valid when the resolved mode is docker.")
    return backend.plan(image=image if backend.accepts_image else None)


def watcher_uninstall_plan(
    mode: WatcherInstallMode | str,
    *,
    path: Path | None = None,
) -> WatcherUninstallPlan:
    """Build a watcher uninstall plan."""
    selected_mode = WatcherInstallMode(mode)
    backend = watcher_class(selected_mode)
    return backend.uninstall_plan(path=path)


def write_watcher_files(plan: WatcherInstallPlan, *, image: str | None = None) -> None:
    """Write watcher files for an install plan."""
    backend = watcher_class(plan.mode)
    backend.write(image=image if backend.accepts_image else None)


def use_scp_for_sync() -> bool:
    """Return whether watcher sync should use scp on this host."""
    return system_is("Windows")


def sync_target(
    context: ContextEntry, local_path: Path, remote_path: str, *, dry_run: bool = False
) -> str:
    """Sync one local file to its remote target."""
    if not context.remote:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    destination = f"{context.remote.user}@{context.remote.host}:{remote_path}"
    if use_scp_for_sync():
        command = scp_upload_command(context.remote, local_path, remote_path)
    else:
        command = ["rsync", "-az", "--protect-args", "-e", rsync_ssh_transport(context.remote)]
        command.extend([str(local_path), destination])
    if dry_run:
        return command_text(command)
    result = run_checked(
        command,
        error_type=ContextError,
        message=f"Sync failed for {local_path}",
        fallback_suggestion="Inspect sync command output.",
    )
    return result.stdout.strip()


def poll_watch(*, interval_seconds: int = 2, dry_run: bool = False) -> None:
    """Poll watch targets and sync changed files forever."""
    seen: dict[tuple[str, Path, str], float] = {}
    while True:
        registry = load_registry()
        by_name = registry.contexts
        for target in derive_watch_targets():
            mtime = target.local_path.stat().st_mtime
            key = (target.context, target.local_path, target.remote_path)
            if seen.get(key) != mtime:
                seen[key] = mtime
                sync_target(
                    by_name[target.context], target.local_path, target.remote_path, dry_run=dry_run
                )
        time.sleep(interval_seconds)
