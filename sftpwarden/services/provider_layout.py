"""Explicit, recoverable migration from single-file Compose provider mounts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sftpwarden.config import (
    FILE_PROVIDER_TYPES,
    ProviderType,
    SFTPWardenConfig,
    dump_config,
    load_config,
    provider_local_path,
)
from sftpwarden.providers import provider_for_config
from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.persistence import (
    atomic_bytes,
    file_lock,
    migration_marker,
    pending_deploy_marker,
    reject_symlink,
    sync_directory,
)
from sftpwarden.utils.sqlite import snapshot_sqlite


@dataclass(frozen=True)
class ProviderLayout:
    """Desired dedicated input location; planning never writes any files."""

    source: Path
    destination: Path
    runtime_path: str

    @property
    def needs_migration(self) -> bool:
        """Return whether the physical provider file changes location."""
        return self.source.absolute() != self.destination.absolute()


def provider_layout(root: Path, config: SFTPWardenConfig) -> ProviderLayout | None:
    """Plan the directory mount without opening or changing the provider."""
    if config.provider.type not in FILE_PROVIDER_TYPES:
        return None
    source = provider_local_path(root, config)
    name = source.name
    return ProviderLayout(
        source=source,
        destination=root / "provider" / name,
        runtime_path=str(PurePosixPath(CONTAINER_PROVIDER_DATA_DIR) / name),
    )


def validate_provider_directory(path: Path) -> None:
    """Refuse unrelated input alongside a provider before mounting its directory."""
    reject_symlink(path.parent)
    allowed = {path.name, f".{path.name}.lock", pending_deploy_marker(path).name}
    allowed.update(f"{path.name}{suffix}" for suffix in ("-wal", "-shm", "-journal"))
    if path.parent.exists():
        for child in path.parent.iterdir():
            reject_symlink(child)
            if child.name not in allowed or not child.is_file():
                raise ProviderError(
                    "Dedicated provider directory contains unrelated files.",
                    suggestion="Keep only the active provider and its database/lock files there.",
                )


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _source_digest(path: Path) -> str:
    """Include SQLite WAL content when checking an interrupted migration."""
    digest = hashlib.sha256(path.read_bytes())
    wal = path.with_name(f"{path.name}-wal")
    if wal.exists():
        reject_symlink(wal)
        digest.update(wal.read_bytes())
    return digest.hexdigest()


def _snapshot(path: Path, config: SFTPWardenConfig, target: Path) -> None:
    """Copy a validated provider, including committed SQLite WAL transactions."""
    if config.provider.type != ProviderType.SQLITE:
        atomic_bytes(target, path.read_bytes())
        return
    snapshot_sqlite(path, target)


def migrate_provider_layout(root: Path, config_path: Path) -> SFTPWardenConfig:
    """Publish a provider copy before committing its config path, retaining recovery material.

    The journal makes retries safe after interruption. Conflicting files are
    rejected rather than overwritten. The old provider remains recovery input;
    application writes to that old path are rejected after the commit marker.
    """
    with file_lock(config_path):
        config = load_config(config_path)
        layout = provider_layout(root, config)
        if layout is None:
            return config
        reject_symlink(config_path)
        reject_symlink(layout.source)
        validate_provider_directory(layout.destination)
        if not layout.needs_migration:
            provider_for_config(config.provider, layout.source).read()
            if config.provider.path != layout.runtime_path:
                config.provider.path = layout.runtime_path
                atomic_bytes(config_path, dump_config(config).encode())
            return config
        with file_lock(layout.source), file_lock(layout.destination):
            provider_for_config(config.provider, layout.source).read()
            return _commit_migration(root, config_path, config, layout)


def _commit_migration(
    root: Path, config_path: Path, config: SFTPWardenConfig, layout: ProviderLayout
) -> SFTPWardenConfig:
    original = config_path.read_bytes()
    source_digest = _source_digest(layout.source)
    recovery = root / ".sftpwarden" / "provider-migration"
    reject_symlink(recovery.parent)
    reject_symlink(recovery)
    metadata = {
        "version": 1,
        "source": str(layout.source.absolute()),
        "destination": str(layout.destination.absolute()),
        "config_digest": _digest(original),
        "source_digest": source_digest,
    }
    if not recovery.exists():
        if layout.destination.exists():
            raise ProviderError(
                "Provider migration destination already exists; refusing to overwrite."
            )
        recovery.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".provider-migration-", dir=recovery.parent
        ) as folder:
            staging = Path(folder)
            _snapshot(layout.source, config, staging / "provider")
            provider_for_config(config.provider, staging / "provider").read()
            if _source_digest(layout.source) != source_digest:
                raise ProviderError(
                    "Provider changed during migration; stop external writers and retry."
                )
            atomic_bytes(staging / "config.yaml", original)
            metadata["snapshot_digest"] = _digest((staging / "provider").read_bytes())
            atomic_bytes(staging / "journal.json", json.dumps(metadata).encode())
            os.replace(staging, recovery)
            sync_directory(recovery.parent)
    try:
        journal = json.loads((recovery / "journal.json").read_bytes())
        snapshot = (recovery / "provider").read_bytes()
        valid = (
            all(journal.get(key) == value for key, value in metadata.items())
            and journal.get("snapshot_digest") == _digest(snapshot)
            and _digest((recovery / "config.yaml").read_bytes()) == metadata["config_digest"]
        )
    except (OSError, ValueError, AttributeError) as exc:
        raise ProviderError(
            "Provider migration recovery material is incomplete or invalid."
        ) from exc
    if not valid:
        raise ProviderError(
            "Provider migration inputs conflict with its recovery journal.",
            suggestion="Preserve both copies and reconcile the interrupted migration before retry.",
        )
    reject_symlink(layout.destination)
    if layout.destination.exists() and _digest(layout.destination.read_bytes()) != _digest(
        snapshot
    ):
        raise ProviderError("Provider migration destination changed; refusing to overwrite.")
    atomic_bytes(layout.destination, snapshot)
    provider_for_config(config.provider, layout.destination).read()
    atomic_bytes(
        pending_deploy_marker(layout.destination), b"Retry deploy until runtime readiness.\n"
    )
    atomic_bytes(migration_marker(layout.source), layout.runtime_path.encode())
    config.provider.path = layout.runtime_path
    atomic_bytes(config_path, dump_config(config).encode())
    return config


def complete_provider_layout(root: Path, config: SFTPWardenConfig) -> None:
    """Release the migrated provider only after deployment commands report readiness."""
    layout = provider_layout(root, config)
    if layout is None or layout.needs_migration:
        return
    with file_lock(layout.destination):
        pending_deploy_marker(layout.destination).unlink(missing_ok=True)
        sync_directory(layout.destination.parent)
