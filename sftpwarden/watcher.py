from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from sftpwarden.config import (
    WATCHER_SYNC_PROVIDER_TYPES,
    SFTPWardenConfig,
    load_config,
    provider_local_path,
)
from sftpwarden.config.global_config import load_global_config, save_global_config
from sftpwarden.contexts import ContextEntry, ContextType, load_registry
from sftpwarden.remote.ssh import (
    explicit_ssh_key_path,
    rsync_ssh_transport,
    uses_default_ssh_identity,
)
from sftpwarden.system.commands import command_text, run_checked
from sftpwarden.utils.collections import unique_items
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.paths import app_home, contexts_path


class WatcherInstallMode(StrEnum):
    """Supported watcher installation modes."""

    SYSTEMD = "systemd"
    DOCKER = "docker"


@dataclass(frozen=True)
class WatchTarget:
    """File that should be watched and synced to a remote host."""

    context: str
    local_path: Path
    remote_path: str


@dataclass(frozen=True)
class WatcherInstallPlan:
    """Files and commands needed to install the watcher."""

    mode: WatcherInstallMode
    path: Path
    commands: list[list[str]]

    def text(self) -> str:
        """Render the watcher install plan for dry-run output.

        Returns
        -------
        str
            Human-readable install plan.
        """
        rendered = [f"{self.mode.value} watcher: {self.path}"]
        rendered.extend(" ".join(command) for command in self.commands)
        return "\n".join(rendered)


def remote_root_path(context: ContextEntry, local_path: Path) -> str:
    """Map a local project file to its remote path.

    Parameters
    ----------
    context
        Remote local-sync context.
    local_path
        Local file path inside the context root.

    Returns
    -------
    str
        Remote file path.
    """
    if not context.remote:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    if not context.root:
        raise ContextError(f"Context {context.name} is missing local root.")
    relative_path = local_path.resolve().relative_to(Path(context.root).resolve())
    return f"{context.remote.remote_root.rstrip('/')}/{relative_path.as_posix()}"


def editable_sync_target(context: ContextEntry, config: SFTPWardenConfig) -> WatchTarget | None:
    """Return the user-provider file that should be synced for a context.

    Parameters
    ----------
    context
        Remote context.
    config
        Project config.

    Returns
    -------
    WatchTarget | None
        Existing user-provider file to sync, or ``None`` when the context has no local
        editable user file.
    """
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
    """Derive watch targets from the context registry.

    Returns
    -------
    list[WatchTarget]
        Sorted remote local-sync watch targets.
    """
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
    """Return watcher status as human-readable text.

    Returns
    -------
    str
        Status text.
    """
    data = watcher_status_data()
    lines = [
        f"Watcher installed: {data['installed']}",
        f"Watcher mode: {data['mode'] or ''}",
        f"Watcher path: {data['path'] or ''}",
        f"Remote local-sync targets: {len(data['targets'])}",
    ]
    lines.extend(f"- {target['context']}: {target['local_path']}" for target in data["targets"])
    return "\n".join(lines)


def watcher_status_data() -> dict:
    """Return watcher status as structured data.

    Returns
    -------
    dict
        JSON-serializable watcher status.
    """
    state = load_global_config().watcher
    targets = derive_watch_targets()
    return {
        "installed": state.installed,
        "mode": state.mode,
        "path": state.path,
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
    """Return the configured default watcher mode.

    Returns
    -------
    WatcherInstallMode
        Default watcher mode.
    """
    mode = load_global_config().defaults.watcher_mode
    return WatcherInstallMode(mode)


def installed_watcher_mode() -> WatcherInstallMode | None:
    """Return the installed watcher mode.

    Returns
    -------
    WatcherInstallMode | None
        Installed mode, or ``None`` when no watcher is installed.
    """
    state = load_global_config().watcher
    if not state.installed or not state.mode:
        return None
    return WatcherInstallMode(state.mode)


def install_watcher(
    *,
    mode: str | WatcherInstallMode | None = None,
    yes: bool = False,
    dry_run: bool = False,
    image: str | None = None,
    activate: bool = False,
) -> str:
    """Install watcher files and optionally activate them.

    Parameters
    ----------
    mode
        Requested watcher mode.
    yes
        Whether to replace existing watcher metadata without prompting.
    dry_run
        Whether to return the install plan without writing files.
    image
        Optional Docker watcher image.
    activate
        Whether to run enable/start commands.

    Returns
    -------
    str
        Install result message.
    """
    selected_mode = WatcherInstallMode(mode or default_watcher_mode())
    config = load_global_config()
    existing = config.watcher
    if existing.installed and existing.mode == selected_mode.value:
        return f"Watcher already installed in {selected_mode.value} mode."
    if existing.installed and existing.mode != selected_mode.value and not yes:
        raise ContextError(
            f"Watcher is already installed in {existing.mode} mode.",
            suggestion="Re-run with --yes to replace it.",
        )
    plan = watcher_install_plan(selected_mode, image=image)
    if dry_run:
        return plan.text()
    write_watcher_files(plan, image=image)
    if activate:
        run_watcher_commands(plan.commands)
    config.watcher.installed = True
    config.watcher.mode = selected_mode.value
    config.watcher.path = str(plan.path)
    save_global_config(config)
    return f"Installed {selected_mode.value} watcher at {plan.path}."


def run_watcher_commands(commands: list[list[str]]) -> None:
    """Run watcher activation commands.

    Parameters
    ----------
    commands
        Commands from a watcher install plan.
    """
    for command in commands:
        run_checked(
            command,
            error_type=ContextError,
            message=f"Watcher command failed: {' '.join(command)}",
            fallback_suggestion="Check sudo permissions and systemd or Docker availability.",
            capture_output=False,
        )


def uninstall_watcher(*, dry_run: bool = False) -> str:
    """Uninstall watcher metadata and generated files.

    Parameters
    ----------
    dry_run
        Whether to report the planned uninstall only.

    Returns
    -------
    str
        Uninstall result message.
    """
    config = load_global_config()
    state = config.watcher
    if not state.installed:
        return "Watcher is not installed."
    path = Path(state.path) if state.path else None
    if dry_run:
        return f"Would uninstall {state.mode} watcher at {path or ''}."
    if path and path.exists():
        path.unlink()
    config.watcher.installed = False
    config.watcher.mode = None
    config.watcher.path = None
    save_global_config(config)
    return "Watcher uninstalled."


def ensure_watcher(
    *,
    requested_mode: str | None = None,
    yes: bool = False,
    image: str | None = None,
) -> str:
    """Ensure a watcher is installed.

    Parameters
    ----------
    requested_mode
        Optional watcher mode to enforce.
    yes
        Whether to replace existing watcher metadata without prompting.
    image
        Optional Docker watcher image.

    Returns
    -------
    str
        Existing or newly installed watcher message.
    """
    config = load_global_config()
    state = config.watcher
    if state.installed and not requested_mode:
        return f"Using existing {state.mode} watcher."
    selected_mode = requested_mode or config.defaults.watcher_mode
    return install_watcher(mode=selected_mode, yes=yes, image=image)


def watcher_install_plan(
    mode: WatcherInstallMode,
    *,
    image: str | None = None,
) -> WatcherInstallPlan:
    """Build a watcher install plan.

    Parameters
    ----------
    mode
        Watcher mode.
    image
        Optional Docker watcher image.

    Returns
    -------
    WatcherInstallPlan
        Install plan.
    """
    if mode == WatcherInstallMode.SYSTEMD:
        return WatcherInstallPlan(
            mode=mode,
            path=systemd_unit_path(),
            commands=[
                [
                    "sudo",
                    "install",
                    "-m",
                    "0644",
                    str(systemd_unit_path()),
                    "/etc/systemd/system/sftpwarden-watch.service",
                ],
                ["sudo", "systemctl", "daemon-reload"],
                ["sudo", "systemctl", "enable", "--now", "sftpwarden-watch.service"],
            ],
        )
    return WatcherInstallPlan(
        mode=mode,
        path=docker_watcher_compose_path(),
        commands=[
            ["docker", "compose", "-f", str(docker_watcher_compose_path()), "up", "-d"],
        ],
    )


def write_watcher_files(plan: WatcherInstallPlan, *, image: str | None = None) -> None:
    """Write watcher files for an install plan.

    Parameters
    ----------
    plan
        Watcher install plan.
    image
        Optional Docker watcher image.
    """
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    if plan.mode == WatcherInstallMode.SYSTEMD:
        plan.path.write_text(render_systemd_unit(), encoding="utf-8")
        return
    plan.path.write_text(render_docker_watcher_compose(image=image), encoding="utf-8")


def systemd_unit_path() -> Path:
    """Return the generated systemd unit path.

    Returns
    -------
    Path
        Systemd unit path inside the app home.
    """
    return app_home() / "watcher" / "systemd" / "sftpwarden-watch.service"


def docker_watcher_compose_path() -> Path:
    """Return the generated Docker watcher compose path.

    Returns
    -------
    Path
        Docker Compose path inside the app home.
    """
    return app_home() / "watcher" / "docker-compose.yml"


def render_systemd_unit() -> str:
    """Render the systemd watcher unit.

    Returns
    -------
    str
        Systemd unit file text.
    """
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "sftpwarden"
    executable = shutil.which("sftpwarden") or "/usr/bin/env sftpwarden"
    return f"""[Unit]
Description=SFTPWarden remote local-sync watcher

[Service]
Type=simple
User={user}
Environment=SFTPWARDEN_HOME={app_home()}
ExecStart={executable} watch
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def render_docker_watcher_compose(*, image: str | None = None) -> str:
    """Render Docker Compose YAML for the watcher.

    Parameters
    ----------
    image
        Optional Docker watcher image.

    Returns
    -------
    str
        Docker Compose YAML text.
    """
    volumes = [
        f"{app_home()}:{app_home()}:ro",
        f"{contexts_path()}:{contexts_path()}:ro",
        *docker_watcher_ssh_volumes(),
    ]
    model = {
        "services": {
            "sftpwarden-watcher": {
                "image": image or "sftpwarden-watcher:local",
                "command": ["sftpwarden", "watch"],
                "volumes": unique_items(volumes),
                "restart": "unless-stopped",
                "read_only": True,
                "security_opt": ["no-new-privileges:true"],
            }
        }
    }
    return yaml.safe_dump(model, sort_keys=False)


def docker_watcher_ssh_volumes() -> list[str]:
    """Return SSH-related Docker volume mounts for watcher contexts.

    Returns
    -------
    list[str]
        Docker volume specifications.

    Raises
    ------
    ContextError
        Raised when Docker watcher would need host default SSH identity.
    """
    volumes: list[str] = []
    for context in docker_watcher_remote_contexts():
        remote = context.remote
        if remote is None:
            continue
        if uses_default_ssh_identity(remote.ssh_key):
            raise ContextError(
                f"Docker watcher cannot use the host default SSH identity for {context.name}.",
                suggestion=(
                    "Use the systemd watcher for host SSH config/agent support, or register "
                    "the context with --ssh-key /path/to/a/dedicated/key."
                ),
            )
        key_path = explicit_ssh_key_path(remote.ssh_key)
        if key_path is None or not key_path.exists():
            raise ContextError(
                f"Docker watcher SSH key not found for {context.name}: {key_path}",
                suggestion="Use an existing dedicated deployment key with --ssh-key.",
            )
        volumes.append(f"{key_path}:{key_path}:ro")
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if volumes and known_hosts.exists():
        volumes.append(f"{known_hosts}:/root/.ssh/known_hosts:ro")
    return volumes


def docker_watcher_remote_contexts() -> list[ContextEntry]:
    """Return remote local-sync contexts relevant to Docker watcher.

    Returns
    -------
    list[ContextEntry]
        Registered remote local-sync contexts.
    """
    registry = load_registry()
    return [
        context
        for context in registry.contexts.values()
        if context.type == ContextType.REMOTE and context.storage == "local-sync" and context.remote
    ]


def sync_target(
    context: ContextEntry, local_path: Path, remote_path: str, *, dry_run: bool = False
) -> str:
    """Sync one local file to its remote target.

    Parameters
    ----------
    context
        Remote context.
    local_path
        Local file to sync.
    remote_path
        Remote destination path.
    dry_run
        Whether to return the rsync command without executing it.

    Returns
    -------
    str
        Rsync output or dry-run command.
    """
    if not context.remote:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    destination = f"{context.remote.user}@{context.remote.host}:{remote_path}"
    command = ["rsync", "-az", "--protect-args", "-e", rsync_ssh_transport(context.remote)]
    command.extend([str(local_path), destination])
    if dry_run:
        return command_text(command)
    result = run_checked(
        command,
        error_type=ContextError,
        message=f"Sync failed for {local_path}",
        fallback_suggestion="Inspect rsync output.",
    )
    return result.stdout.strip()


def poll_watch(*, interval_seconds: int = 2, dry_run: bool = False) -> None:
    """Poll watch targets and sync changed files forever.

    Parameters
    ----------
    interval_seconds
        Poll interval in seconds.
    dry_run
        Whether to print commands without syncing.
    """
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
