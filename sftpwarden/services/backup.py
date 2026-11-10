from __future__ import annotations

import io
import json
import tarfile
import tempfile
from collections.abc import Callable
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from sftpwarden.config import (
    FILE_PROVIDER_TYPES,
    DeployTarget,
    ProviderType,
    SFTPWardenConfig,
    load_config,
    provider_local_path,
)
from sftpwarden.contexts import ContextEntry, ContextType, resolve_context
from sftpwarden.providers import provider_for_config, provider_from_config
from sftpwarden.providers.destination import destination_fingerprint
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.services.backup_inventory import Inventory, OwnershipReader, add_payload, add_source
from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.services.provider_transfer import serialize_users
from sftpwarden.services.restore_target import inspect_empty_target, publish_fresh_files
from sftpwarden.users import users_fingerprint
from sftpwarden.utils._version import get_version
from sftpwarden.utils.archives import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_METADATA_BYTES,
    archive_path_key,
    archive_relative_path,
    check_managed_path,
    check_source_tree,
    extract_private_archive,
)
from sftpwarden.utils.errors import RuntimeError, SFTPWardenError
from sftpwarden.utils.persistence import atomic_binary_output, file_lock
from sftpwarden.utils.sqlite import snapshot_sqlite
from sftpwarden.utils.state_transactions import state_transaction

BACKUP_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class BackupResult:
    """Backup or restore result."""

    path: Path
    entries: list[str]
    safety_backup: Path | None = None
    target_root: Path | None = None
    target_state: str | None = None


def create_backup(
    *,
    context_name: str | None,
    output: str | None,
    include_data: bool = False,
    dry_run: bool = False,
    maintenance: bool = False,
) -> BackupResult:
    """Create a SFTPWarden project backup.

    Parameters
    ----------
    context_name
        Optional context name.
    output
        Optional output path.
    include_data
        Whether to include SFTP user data.
    dry_run
        Whether to avoid writing the archive.

    Returns
    -------
    BackupResult
        Backup result.
    """
    entry = resolve_context(context_name=context_name, read_only=dry_run)
    if entry.type == ContextType.REMOTE:
        from sftpwarden.services.backup_remote import create_remote_runtime_backup

        return create_remote_runtime_backup(
            entry,
            Path(output) if output else default_backup_path(entry.name),
            include_data=include_data,
            dry_run=dry_run,
            maintenance=maintenance,
        )
    root = Path(entry.root)
    config_path = Path(entry.config)
    config = load_config(config_path)
    output_path = Path(output) if output else default_backup_path(entry.name)
    if config.deploy.target == DeployTarget.KUBERNETES:
        from sftpwarden.services.backup_kubernetes import create_kubernetes_backup

        return create_kubernetes_backup(
            config, output_path, include_data=include_data, maintenance=maintenance, dry_run=dry_run
        )
    if maintenance:
        from sftpwarden.services.backup_runtime import create_runtime_backup

        return create_runtime_backup(
            entry,
            output_path,
            include_data=include_data,
            dry_run=dry_run,
        )
    for directory in ("state", "host_keys", *(("data",) if include_data else ())):
        try:
            has_runtime_material = next((root / directory).iterdir(), None) is not None
        except FileNotFoundError:
            has_runtime_material = False
        if has_runtime_material:
            raise RuntimeError(
                "Runtime recovery material requires an explicit --maintenance backup.",
                suggestion="Pause external writers and use --maintenance to capture Linux mounts.",
            )
    entries = backup_entries(root, config, include_data=include_data, config_path=config_path)
    if dry_run:
        return BackupResult(path=output_path, entries=entries)

    write_backup_archive(
        output_path=output_path,
        root=root,
        entries=entries,
        context_name=entry.name,
        provider_type=config.provider.type.value,
        include_data=include_data,
        config_path=config_path,
    )
    return BackupResult(path=output_path, entries=entries)


def restore_backup(
    *,
    context_name: str | None,
    backup_path: str,
    include_data: bool = False,
    dry_run: bool = False,
    target_root: str | None = None,
    maintenance: bool = False,
    provider_context: str | None = None,
) -> BackupResult:
    """Restore a SFTPWarden project backup.

    Parameters
    ----------
    context_name
        Optional context name.
    backup_path
        Backup archive path.
    include_data
        Whether to restore SFTP user data.
    dry_run
        Whether to avoid writing files.

    Returns
    -------
    BackupResult
        Restore result.
    """
    if target_root is not None and context_name is not None:
        raise RuntimeError("Select either --root for fresh recovery or an existing --context.")
    if provider_context is not None and target_root is None and context_name is None:
        raise RuntimeError(
            "--provider-context requires an explicit --root or remote recovery --context."
        )
    if target_root is not None and maintenance:
        raise RuntimeError("Restore maintenance applies to an existing local context, not --root.")
    archive_path = Path(backup_path)
    if not archive_path.exists():
        raise RuntimeError(f"Backup file not found: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="sftpwarden-restore-") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as archive:
            headers = safe_extract(archive, tmp_path, private_staging=True)
        manifest = validate_backup_manifest(tmp_path, headers)
        entries = [
            entry
            for entry in manifest.entries
            if include_data or (entry != "data" and not entry.startswith("data/"))
        ]
        if target_root is not None:
            if provider_context is not None:
                from sftpwarden.services.restore_external import restore_external_fresh

                fresh_root, safety = restore_external_fresh(
                    target_root,
                    provider_context,
                    tmp_path,
                    manifest,
                    entries,
                    dry_run=dry_run,
                )
                if manifest.config.docker.compose_file not in entries:
                    entries.append(manifest.config.docker.compose_file)
                return BackupResult(
                    path=archive_path,
                    entries=entries,
                    target_root=fresh_root,
                    safety_backup=safety,
                    target_state="empty",
                )
            fresh_root = inspect_empty_target(
                target_root,
                manifest,
                source=tmp_path,
                entries=entries,
            )
            if not dry_run:
                publish_fresh_files(fresh_root, tmp_path, entries, manifest)
            return BackupResult(
                path=archive_path,
                entries=entries,
                target_root=fresh_root,
                target_state="empty",
            )
        entry = resolve_context(context_name=context_name, read_only=True)
        if entry.type == ContextType.REMOTE:
            from sftpwarden.services.restore_remote import restore_fresh_remote

            if (
                entry.remote is None
                or entry.remote.compose_file != manifest.config.docker.compose_file
            ):
                raise RuntimeError("Recovery Compose filename differs from the remote context.")
            remote_archive = archive_path
            if manifest.config.provider.type not in FILE_PROVIDER_TYPES:
                from sftpwarden.services.restore_external import protected_provider, rebind_archive

                if provider_context is None:
                    raise RuntimeError(
                        "Remote external recovery requires --provider-context naming "
                        "the separately recovered local project."
                    )
                protected = resolve_context(context_name=provider_context, read_only=True)
                destination_provider = protected_provider(protected, manifest)
                remote_archive = tmp_path / "protected-recovery.tgz"
                rebind_archive(tmp_path, manifest, destination_provider, remote_archive)
            elif provider_context is not None:
                raise RuntimeError("--provider-context only applies to external database recovery.")
            restore_fresh_remote(
                entry,
                remote_archive,
                include_data=include_data,
                dry_run=dry_run,
                protected_external=provider_context is not None,
            )
            return BackupResult(path=archive_path, entries=entries, target_state="empty")
        if provider_context is not None:
            raise RuntimeError(
                "Use --provider-context with fresh --root or remote --context recovery."
            )
        root = Path(entry.root) if entry.root else None
        if root is not None:
            try:
                selected = load_config(entry.config)
            except (OSError, ValueError, SFTPWardenError):
                selected = None  # The damaged-target classifier below retains the original.
            if selected is not None and selected.deploy.target == DeployTarget.KUBERNETES:
                from sftpwarden.services.restore_kubernetes import restore_new_cluster

                restore_new_cluster(
                    entry,
                    selected,
                    archive_path,
                    tmp_path,
                    manifest,
                    include_data=include_data,
                    dry_run=dry_run,
                )
                return BackupResult(path=archive_path, entries=entries, target_state="empty")
            from sftpwarden.services.preserve_damaged import (
                DamagedTargetError,
                preserve_readable_project,
            )
            from sftpwarden.services.restore_existing import restore_existing_files

            try:
                restored_root, safety = restore_existing_files(
                    entry,
                    tmp_path,
                    manifest,
                    entries,
                    include_data=include_data,
                    dry_run=dry_run,
                    maintenance=maintenance,
                )
            except DamagedTargetError:
                if dry_run:
                    raise
                preservation = preserve_readable_project(
                    root,
                    entries,
                    root.resolve().parent / default_backup_path("damaged-preservation").name,
                )
                raise RuntimeError(
                    (
                        "The damaged target was not replaced. Readable files were "
                        "preserved with an omission inventory; this is not a complete "
                        "backup."
                    ),
                    suggestion=(
                        f"Keep the original and {preservation}. "
                        "Restore the validated input into a separate empty --root."
                    ),
                ) from None
            return BackupResult(
                path=archive_path,
                entries=entries,
                safety_backup=safety,
                target_root=restored_root,
                target_state="existing",
            )
        raise RuntimeError("The selected context has no inspectable recovery destination.")


def create_remote_backup(
    *,
    entry: ContextEntry,
    output: str | None,
    include_data: bool,
    dry_run: bool,
    maintenance: bool = False,
) -> BackupResult:
    """Compatibility entry point for authoritative remote maintenance capture."""
    from sftpwarden.services.backup_remote import create_remote_runtime_backup

    return create_remote_runtime_backup(
        entry,
        Path(output) if output else default_backup_path(entry.name),
        include_data=include_data,
        dry_run=dry_run,
        maintenance=maintenance,
    )


def write_backup_archive(
    *,
    output_path: Path | None,
    root: Path,
    entries: list[str],
    context_name: str,
    provider_type: str,
    include_data: bool,
    config_path: Path | None = None,
    stream: BinaryIO | None = None,
    sources: dict[str, Path] | None = None,
    ownership: OwnershipReader | None = None,
    consistency: str = "provider-snapshot-only",
    topology: str = "project-files",
    applied_fingerprint: str | None = None,
) -> None:
    """Write a backup archive from a prepared root directory.

    Parameters
    ----------
    output_path
        Target archive path.
    root
        Directory containing files to archive.
    entries
        Relative entries to include.
    context_name
        Context name stored in manifest.
    provider_type
        Provider type stored in manifest.
    include_data
        Whether the backup includes SFTP data.
    """
    selected_config = config_path or root / "sftpwarden.yaml"
    if (output_path is None) == (stream is None):
        raise RuntimeError("Select exactly one backup output path or stream.")
    with state_transaction(selected_config), ExitStack() as stack:
        config = load_config(selected_config)
        provider = provider_from_config(root, config)
        # A failed required provider read must propagate before publication.
        users = require_configured_schema(provider)
        physical_snapshot = None
        provider_path = None
        if config.provider.type in FILE_PROVIDER_TYPES:
            provider_path = provider_local_path(root, config)
            stack.enter_context(file_lock(provider_path))
            check_source_tree(provider_path)
            if config.provider.type == ProviderType.SQLITE:
                staging = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(
                            prefix="sftpwarden-provider-snapshot-",
                        )
                    )
                )
                physical_snapshot = staging / "provider.sqlite"
                snapshot_sqlite(provider_path, physical_snapshot)
                users = require_configured_schema(
                    provider_for_config(config.provider, physical_snapshot)
                )
            else:
                users = require_configured_schema(provider)
        if applied_fingerprint is not None and users_fingerprint(users) != applied_fingerprint:
            raise RuntimeError(
                "Provider policy differs from the applied runtime; reconcile before capture."
            )

        def verify_policy() -> None:
            if require_configured_schema(provider) != users:
                raise RuntimeError(
                    "Provider changed during recovery capture; no archive was published."
                )

        _write_prepared_archive(
            output_path=output_path,
            root=root,
            entries=entries,
            context_name=context_name,
            provider_type=provider_type,
            include_data=include_data,
            config_path=selected_config,
            users_json=serialize_users(users, "json"),
            provider_path=provider_path,
            physical_snapshot=physical_snapshot,
            stream=stream,
            sources=sources or {},
            ownership=ownership,
            consistency=consistency,
            topology=topology,
            verify_policy=verify_policy,
            provider_destination=destination_fingerprint(config.provider),
        )


def _write_prepared_archive(
    *,
    output_path: Path | None,
    root: Path,
    entries: list[str],
    context_name: str,
    provider_type: str,
    include_data: bool,
    config_path: Path,
    users_json: str,
    provider_path: Path | None,
    physical_snapshot: Path | None,
    stream: BinaryIO | None,
    sources: dict[str, Path],
    ownership: OwnershipReader | None,
    consistency: str,
    topology: str,
    verify_policy: Callable[[], None] | None = None,
    provider_destination: str | None = None,
) -> None:
    """Publish only a complete supported archive, preserving any old output on failure."""
    normalized: dict[str, str] = {}
    for relative in entries:
        path = archive_relative_path(relative)
        name = path.as_posix()
        key = archive_path_key(path)
        if key in normalized or key in {"manifest.json", "provider/users.json"}:
            raise RuntimeError("Backup entries conflict with archive recovery metadata.")
        normalized[key] = name
        source = sources.get(name) or check_managed_path(root, name)
        check_source_tree(source)
        if output_path is not None and (
            output_path.resolve() == source.resolve()
            or (source.is_dir() and source.resolve() in output_path.resolve().parents)
        ):
            raise RuntimeError(
                "Backup output must be outside the selected source files/directories."
            )
    required = [config_path.relative_to(root).as_posix()]
    if provider_path is not None:
        required.append(provider_path.relative_to(root).as_posix())
    if any(name not in normalized.values() for name in required):
        raise RuntimeError("Backup entries omit required configuration or provider files.")
    manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "sftpwarden_version": get_version(),
        "context": context_name,
        "provider": provider_type,
        "include_data": include_data,
        "entries": entries,
        "config_path": config_path.relative_to(root).as_posix(),
        "consistency": consistency,
        "topology": topology,
        "provider_destination": provider_destination,
    }
    inventory: Inventory = {}
    with (
        (
            atomic_binary_output(output_path) if output_path is not None else nullcontext(stream)
        ) as output_stream,
        tarfile.open(fileobj=output_stream, mode="w:gz") as archive,
    ):
        for relative in entries:
            source = sources.get(relative) or root / relative
            if source == provider_path and physical_snapshot is not None:
                source = physical_snapshot
            add_source(archive, source, relative, inventory, ownership=ownership)
        add_payload(archive, "provider/users.json", users_json.encode("utf-8"), inventory)
        if verify_policy is not None:
            verify_policy()
        manifest["members"] = inventory
        manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        metadata_size = len(manifest_text.encode("utf-8"))
        if (
            len(inventory) + 1 > MAX_ARCHIVE_MEMBERS
            or sum(int(item["size"]) for item in inventory.values()) + metadata_size
            > MAX_ARCHIVE_BYTES
            or max(metadata_size, len(users_json.encode("utf-8"))) > MAX_METADATA_BYTES
        ):
            raise RuntimeError("Backup exceeds the supported extraction limits.")
        add_text(archive, "manifest.json", manifest_text)


def remote_backup_candidates(compose_file: str, *, include_data: bool) -> list[str]:
    """Return candidate remote paths for backup.

    Parameters
    ----------
    compose_file
        Remote Compose file name.
    include_data
        Whether SFTP user data should be included.

    Returns
    -------
    list[str]
        Candidate relative paths.
    """
    candidates = [
        "sftpwarden.yaml",
        compose_file,
        "users.yaml",
        "users.csv",
        "users.sqlite",
        "host_keys",
        "state",
    ]
    if include_data:
        candidates.append("data")
    return sorted(dict.fromkeys(candidates))


def backup_entries_from_root(root: Path, *, compose_file: str, include_data: bool) -> list[str]:
    """Return backup entries that exist under a prepared root.

    Parameters
    ----------
    root
        Prepared root directory.
    compose_file
        Compose file name to look for.
    include_data
        Whether data entries should be included.

    Returns
    -------
    list[str]
        Existing relative entries.
    """
    entries = []
    for candidate in remote_backup_candidates(compose_file, include_data=include_data):
        path = root / candidate
        if path.exists() and (include_data or candidate != "data"):
            entries.append(candidate)
    return sorted(dict.fromkeys(entries))


def backup_entries(
    root: Path,
    config: SFTPWardenConfig,
    *,
    include_data: bool,
    config_path: Path | None = None,
) -> list[str]:
    """Return backup entry paths.

    Parameters
    ----------
    root
        Project root.
    config
        Project config.
    include_data
        Whether to include user data.

    Returns
    -------
    list[str]
        Relative archive entries.
    """
    config_relative = (config_path or root / "sftpwarden.yaml").relative_to(root).as_posix()
    entries = [config_relative, config.docker.compose_file]
    if config.provider.type in FILE_PROVIDER_TYPES:
        provider_path = provider_local_path(root, config)
        entries.append(provider_path.relative_to(root).as_posix())
    for directory in ("host_keys", "state"):
        if (root / directory).exists():
            entries.append(directory)
    if include_data and (root / "data").exists():
        entries.append("data")
    return sorted(dict.fromkeys(entries))


def default_backup_path(context_name: str) -> Path:
    """Return a default backup path.

    Parameters
    ----------
    context_name
        Context name.

    Returns
    -------
    Path
        Default backup path.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return Path(f"sftpwarden-{context_name}-{stamp}-{uuid4().hex}.tar.gz")


def add_json(archive: tarfile.TarFile, name: str, data: dict[str, Any]) -> None:
    """Add JSON data to an archive."""
    add_text(archive, name, json.dumps(data, indent=2, sort_keys=True) + "\n")


def add_text(archive: tarfile.TarFile, name: str, text: str) -> None:
    """Add text to an archive."""
    encoded = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(encoded)
    info.mode = 0o600
    archive.addfile(info, fileobj=io.BytesIO(encoded))


def safe_extract(
    archive: tarfile.TarFile,
    destination: Path,
    *,
    private_staging: bool = False,
) -> dict[str, tarfile.TarInfo]:
    """Extract a tar archive without allowing path traversal.

    Parameters
    ----------
    archive
        Archive to extract.
    destination
        Destination directory.
    """
    return extract_private_archive(archive, destination, private_staging=private_staging)
