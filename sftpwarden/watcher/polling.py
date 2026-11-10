"""Content-based watcher polling with durable per-target delivery and refresh progress."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from sftpwarden.config import (
    WATCHER_SYNC_PROVIDER_TYPES,
    LoggingConfig,
    load_config,
    provider_local_path,
)
from sftpwarden.contexts import ContextEntry, ContextType, load_registry
from sftpwarden.refresh import refresh_context
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.event_log import service_event
from sftpwarden.utils.paths import app_home
from sftpwarden.utils.persistence import atomic_bytes, file_lock, reject_symlink
from sftpwarden.watcher.delivery import deliver_snapshot, read_snapshot


class DeliveryState(BaseModel):
    """Hashes only: provider credentials/content never enter watcher diagnostics."""

    model_config = ConfigDict(extra="forbid")
    format: int = 1
    delivered: str = ""
    applied: str = ""
    policy: str = ""
    refresh_pending: bool = False
    phase: str = "pending"
    failures: int = 0
    error: str = ""


def checkpoint_path(context: ContextEntry, path: Path) -> Path:
    """Retargeting cannot reuse an acknowledgement from another server or provider path."""
    # Docker rewrites host roots and private-key paths. The project-relative input
    # and deployment endpoint are stable on both sides of those bind mounts.
    relative = path.absolute().relative_to(Path(context.root).absolute()).as_posix()
    remote = context.remote.model_dump(mode="json", exclude={"ssh_key"}) if context.remote else None
    identity = json.dumps(
        {
            "context": context.name,
            "provider": relative,
            "remote": remote,
        },
        sort_keys=True,
    ).encode()
    return app_home() / "watcher" / "checkpoints" / (hashlib.sha256(identity).hexdigest() + ".json")


def load_checkpoint(path: Path) -> DeliveryState:
    """Missing progress retries delivery; corrupt progress is reported, never trusted."""
    reject_symlink(path)
    try:
        state = DeliveryState.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return DeliveryState()
    if state.format != 1:
        raise ContextError("Unsupported watcher checkpoint version.")
    return state


def checkpoint_owner(path: Path) -> tuple[int, int] | None:
    """Keep container-written checkpoints private and usable by the host operator."""
    if os.environ.get("SFTPWARDEN_SHARED_CHECKPOINTS") != "1" or os.name != "posix":
        return None
    # Only the Docker backend enables this. Its writable checkpoint directory
    # is created by the host operator and mounted independently of the registry.
    # Inspect that directory, never ownership supplied by a provider document.
    reject_symlink(path.parent)
    info = path.parent.stat()
    return info.st_uid, info.st_gid


def save_checkpoint(path: Path, state: DeliveryState) -> None:
    """Publish private progress with the shared directory's operator ownership."""
    atomic_bytes(path, state.model_dump_json().encode(), owner=checkpoint_owner(path))


class WatchPoller:
    """Debounce independently and retry failures without requiring another provider edit."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.observed: dict[Path, str] = {}
        self.retry_at: dict[Path, float] = {}
        self.previewed: dict[Path, str] = {}
        self.last_error: dict[str, float] = {}
        self.logging: dict[str, LoggingConfig] = {}

    def poll(self) -> None:
        """One bounded pass; malformed input or an unavailable host cannot end the watcher."""
        try:
            contexts = load_registry().contexts.values()
        except Exception:  # noqa: BLE001
            service_event(
                LoggingConfig(),
                "error",
                "Watcher registry could not be read; retaining delivery checkpoints.",
            )
            return
        for context in contexts:
            if (
                context.type != ContextType.REMOTE
                or context.storage != "local-sync"
                or context.remote is None
                or not context.root
                or not context.config
            ):
                continue
            try:
                self._target(context)
            except Exception:  # noqa: BLE001
                # Exception strings may include provider contents or remote output.
                now = time.monotonic()
                if now - self.last_error.get(context.name, -60) >= 60:
                    service_event(
                        self.logging.get(context.name, LoggingConfig()),
                        "error",
                        "Watcher delivery failed; pending delivery will be retried.",
                        context=context.name,
                    )
                    self.last_error[context.name] = now

    def _target(self, context: ContextEntry) -> None:
        if not context.config or not context.root or context.remote is None:
            raise ContextError("Watcher delivery requires a configured remote local-sync target.")
        config = load_config(context.config)
        self.logging[context.name] = config.logging
        if config.provider.type not in WATCHER_SYNC_PROVIDER_TYPES:
            return
        path = provider_local_path(context.root, config)
        checkpoint = checkpoint_path(context, path)
        try:
            snapshot = read_snapshot(context, config)
        except Exception:
            self.observed.pop(checkpoint, None)
            if not self.dry_run:
                with file_lock(checkpoint, owner=checkpoint_owner(checkpoint)):
                    state = load_checkpoint(checkpoint)
                    state.phase = "input_failed"
                    state.error = "Provider input is missing, invalid or changing; retry pending."
                    save_checkpoint(checkpoint, state)
            raise
        if self.observed.get(checkpoint) != snapshot.digest:
            self.observed[checkpoint] = snapshot.digest
            self.retry_at.pop(checkpoint, None)
            return
        remote_path = (
            f"{context.remote.remote_root.rstrip('/')}/"
            f"{path.relative_to(Path(context.root)).as_posix()}"
        )
        if self.dry_run:
            if self.previewed.get(checkpoint) != snapshot.digest:
                deliver_snapshot(context, config, remote_path, snapshot, dry_run=True)
                # Dry-run is command output, independent of the service log threshold.
                from sftpwarden.utils.console import console

                console.print(f"Would deliver validated provider changes for {context.name}.")
                self.previewed[checkpoint] = snapshot.digest
            return
        if time.monotonic() < self.retry_at.get(checkpoint, 0):
            return
        with file_lock(checkpoint, owner=checkpoint_owner(checkpoint)):
            current = read_snapshot(context, config)
            if current.digest != snapshot.digest:
                self.observed[checkpoint] = current.digest
                return
            state = load_checkpoint(checkpoint)
            if state.applied == snapshot.digest and not state.refresh_pending:
                if state.error and not state.refresh_pending:
                    state.error = ""
                    state.phase = "applied"
                    state.failures = 0
                    save_checkpoint(checkpoint, state)
                return
            try:
                needs_refresh = state.refresh_pending or state.policy != snapshot.policy
                # Retry publication too: a remote restart/operator action may have
                # changed the file since a prior successful delivery acknowledgement.
                deliver_snapshot(context, config, remote_path, snapshot)
                state.delivered = snapshot.digest
                state.refresh_pending = needs_refresh
                state.phase = "refresh_pending" if needs_refresh else "delivery_complete"
                save_checkpoint(checkpoint, state)
                if needs_refresh:
                    refresh_context(context, timeout=60)
                state.applied = snapshot.digest
                state.policy = snapshot.policy
                state.refresh_pending = False
                state.phase = "applied"
                state.failures = 0
                state.error = ""
                save_checkpoint(checkpoint, state)
                self.retry_at.pop(checkpoint, None)
                service_event(
                    config.logging,
                    "info",
                    "Provider delivery and required policy refresh completed.",
                    context=context.name,
                )
            except Exception:  # noqa: BLE001
                state.failures = min(state.failures + 1, 10)
                state.phase = "refresh_pending" if state.refresh_pending else "delivery_failed"
                state.error = "Delivery or refresh failed; retry pending."
                save_checkpoint(checkpoint, state)
                self.retry_at[checkpoint] = time.monotonic() + min(60, 2**state.failures)
                raise
