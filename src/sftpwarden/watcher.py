from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sftpwarden.config import load_config, provider_local_path
from sftpwarden.contexts import ContextEntry, ContextType, load_registry
from sftpwarden.utils.errors import ContextError
from sftpwarden.remote.ssh import uses_default_ssh_identity
from sftpwarden.utils.constants import IGNORED_WATCH_PARTS, WATCHED_FILENAMES


@dataclass(frozen=True)
class WatchTarget:
    context: str
    local_path: Path
    remote_path: str


def should_watch(path: Path) -> bool:
    if any(part in IGNORED_WATCH_PARTS for part in path.parts):
        return False
    return path.name in WATCHED_FILENAMES


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
        provider_path = provider_local_path(context.root, config)
        for local_path in {config_path, provider_path}:
            if should_watch(local_path):
                remote_path = f"{context.remote.remote_root.rstrip('/')}/{local_path.name}"
                targets.append(
                    WatchTarget(
                        context=context.name, local_path=local_path, remote_path=remote_path
                    )
                )
    return targets


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
    seen: dict[Path, float] = {}
    while True:
        registry = load_registry()
        by_name = registry.contexts
        for target in derive_watch_targets():
            mtime = target.local_path.stat().st_mtime
            if seen.get(target.local_path) != mtime:
                seen[target.local_path] = mtime
                sync_target(
                    by_name[target.context], target.local_path, target.remote_path, dry_run=dry_run
                )
        time.sleep(interval_seconds)
