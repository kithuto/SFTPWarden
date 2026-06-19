from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from sftpwarden.config import ProviderType, SFTPWardenConfig, load_config, provider_local_path
from sftpwarden.config.global_config import load_global_config, save_global_config
from sftpwarden.contexts import ContextEntry, ContextType, load_registry
from sftpwarden.remote.ssh import uses_default_ssh_identity
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.paths import app_home, contexts_path

FILE_PROVIDER_TYPES = {ProviderType.YAML, ProviderType.CSV}


class WatcherInstallMode(StrEnum):
    SYSTEMD = "systemd"
    DOCKER = "docker"


@dataclass(frozen=True)
class WatchTarget:
    context: str
    local_path: Path
    remote_path: str


@dataclass(frozen=True)
class WatcherInstallPlan:
    mode: WatcherInstallMode
    path: Path
    commands: list[list[str]]

    def text(self) -> str:
        rendered = [f"{self.mode.value} watcher: {self.path}"]
        rendered.extend(" ".join(command) for command in self.commands)
        return "\n".join(rendered)


def remote_root_path(context: ContextEntry, local_path: Path) -> str:
    if not context.remote:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    if not context.root:
        raise ContextError(f"Context {context.name} is missing local root.")
    relative_path = local_path.resolve().relative_to(Path(context.root).resolve())
    return f"{context.remote.remote_root.rstrip('/')}/{relative_path.as_posix()}"


def editable_sync_targets(context: ContextEntry, config: SFTPWardenConfig) -> list[WatchTarget]:
    if not context.remote:
        return []
    config_path = Path(context.config)
    candidates = [
        WatchTarget(
            context=context.name,
            local_path=config_path,
            remote_path=context.remote.remote_config,
        )
    ]
    if config.provider.type in FILE_PROVIDER_TYPES:
        provider_path = provider_local_path(context.root, config)
        candidates.append(
            WatchTarget(
                context=context.name,
                local_path=provider_path,
                remote_path=remote_root_path(context, provider_path),
            )
        )
    return [target for target in candidates if target.local_path.exists()]


def derive_watch_targets() -> list[WatchTarget]:
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
        targets.extend(editable_sync_targets(context, config))
    return sorted(targets, key=lambda target: (target.context, str(target.local_path)))


def watcher_status_text() -> str:
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
    mode = load_global_config().defaults.watcher_mode
    return WatcherInstallMode(mode)


def installed_watcher_mode() -> WatcherInstallMode | None:
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
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise ContextError(
                f"Watcher command failed: {' '.join(command)}",
                suggestion="Check sudo permissions and systemd or Docker availability.",
            )


def uninstall_watcher(*, dry_run: bool = False) -> str:
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
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    if plan.mode == WatcherInstallMode.SYSTEMD:
        plan.path.write_text(render_systemd_unit(), encoding="utf-8")
        return
    plan.path.write_text(render_docker_watcher_compose(image=image), encoding="utf-8")


def systemd_unit_path() -> Path:
    return app_home() / "watcher" / "systemd" / "sftpwarden-watch.service"


def docker_watcher_compose_path() -> Path:
    return app_home() / "watcher" / "docker-compose.yml"


def render_systemd_unit() -> str:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "sftpwarden"
    return f"""[Unit]
Description=SFTPWarden remote local-sync watcher

[Service]
Type=simple
User={user}
Environment=SFTPWARDEN_HOME={app_home()}
ExecStart=sftpwarden watch
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def render_docker_watcher_compose(*, image: str | None = None) -> str:
    model = {
        "services": {
            "sftpwarden-watcher": {
                "image": image or "sftpwarden-watcher:local",
                "command": ["sftpwarden", "watch"],
                "volumes": [
                    f"{app_home()}:{app_home()}:ro",
                    f"{contexts_path()}:{contexts_path()}:ro",
                    f"{Path.home() / '.ssh'}:/home/sftpwarden/.ssh:ro",
                ],
                "restart": "unless-stopped",
                "read_only": True,
                "security_opt": ["no-new-privileges:true"],
            }
        }
    }
    return yaml.safe_dump(model, sort_keys=False)


def sync_target(
    context: ContextEntry, local_path: Path, remote_path: str, *, dry_run: bool = False
) -> str:
    if not context.remote:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    destination = f"{context.remote.user}@{context.remote.host}:{remote_path}"
    command = ["rsync", "-az", "--protect-args", "-e", f"ssh -p {context.remote.port}"]
    if not uses_default_ssh_identity(context.remote.ssh_key):
        command[-1] = f"{command[-1]} -i {context.remote.ssh_key}"
    command.extend([str(local_path), destination])
    if dry_run:
        return " ".join(command)
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise ContextError(f"Sync failed for {local_path}", suggestion=result.stderr.strip())
    return result.stdout.strip()


def poll_watch(*, interval_seconds: int = 2, dry_run: bool = False) -> None:
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
