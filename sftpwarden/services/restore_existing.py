"""Inspect and journal replacement of files in an existing local project."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path, PurePosixPath

import yaml

from sftpwarden.config import (
    FILE_PROVIDER_TYPES,
    DeployTarget,
    ProviderType,
    load_config,
    provider_local_path,
)
from sftpwarden.contexts import ContextEntry, ContextType
from sftpwarden.providers import BaseProvider, provider_from_config
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.render.compose import compose_model
from sftpwarden.runtime.core import RuntimeState
from sftpwarden.runtime.recovery import BLOCK_FILE, write_recovery_record
from sftpwarden.services.backup_manifest import ValidatedBackup, validate_backup_manifest
from sftpwarden.services.backup_runtime import (
    RuntimeCapture,
    capture_runtime,
    inspect_runtime,
    stopped_runtime,
)
from sftpwarden.services.preserve_damaged import DamagedTargetError
from sftpwarden.services.restore_target import recovery_metadata
from sftpwarden.services.schema_transition import require_no_pending_transition
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.archives import (
    check_managed_path,
    check_source_tree,
    extract_private_archive,
)
from sftpwarden.utils.errors import RuntimeError, SFTPWardenError
from sftpwarden.utils.persistence import (
    atomic_binary_output,
    atomic_bytes,
    file_lock,
    sync_directory,
)
from sftpwarden.utils.state_transactions import state_transaction

JOURNAL = ".sftpwarden/restore-transaction.json"
# ':' cannot occur in a portable archive member, so this never aliases a file step.
PROVIDER_STEP = "provider:external"
EXTERNAL_RECOVERY = (
    "Preserve the selected database and safety archive. Inspect completed/pending steps; "
    "extract provider/users.json privately from the verified safety archive and, after "
    "confirming the destination, use provider import --replace --no-refresh to recover "
    "its policy. Reconcile project files before explicitly activating the runtime."
)


def inspect_existing_target(
    entry: ContextEntry,
    source: Path,
    manifest: ValidatedBackup,
    entries: list[str],
) -> Path:
    """Reject ambiguous paths and damaged targets without turning absence into permission."""
    try:
        return _inspect_existing_target(entry, source, manifest, entries)
    except PermissionError:
        raise RuntimeError(
            "Restore cannot inspect protected files with this operator's permissions.",
            suggestion=(
                "Use an administrator with read/write access to the selected project and "
                "runtime files, or recover into a separate empty directory with --root. "
                "The original installation has not been replaced."
            ),
        ) from None


def _inspect_existing_target(
    entry: ContextEntry, source: Path, manifest: ValidatedBackup, entries: list[str]
) -> Path:
    root = Path(entry.root).absolute()
    if entry.type != ContextType.LOCAL:
        raise RuntimeError("Local restore cannot replace the storage of a remote context.")
    check_managed_path(root, JOURNAL)
    journal = root / JOURNAL
    if journal.exists():
        try:
            record = json.loads(journal.read_text(encoding="utf-8"))
            complete = record["phase"] == "files_restored" and record["pending"] == []
            external = record.get("external_provider", False)
        except (OSError, ValueError, KeyError, TypeError):
            complete = False
            external = False
        if not complete:
            raise RuntimeError(
                "An interrupted restore requires recovery before another in-place attempt.",
                suggestion=EXTERNAL_RECOVERY
                if external
                else (
                    "Inspect the private restore journal and restore its safety archive "
                    "into a separate empty directory with --root. Keep this target stopped."
                ),
            )
    config_path = Path(entry.config).absolute()
    if config_path != root / manifest.config_path:
        raise RuntimeError("Restore configuration destination differs from this context.")
    check_managed_path(root, manifest.config_path)
    require_no_pending_transition(entry)
    try:
        current = load_config(config_path)
    except (OSError, ValueError, SFTPWardenError):
        raise DamagedTargetError(
            "The existing target is damaged; recover into a separate empty directory with --root."
        ) from None
    if (
        current.deploy.target != DeployTarget.COMPOSE
        or current.remote.enabled
        or manifest.config.deploy.target != DeployTarget.COMPOSE
        or manifest.config.remote.enabled
    ):
        raise RuntimeError("This file restoration route requires a local Compose destination.")
    if (
        manifest.config.provider.type != current.provider.type
        or current.docker.compose_file != manifest.config.docker.compose_file
    ):
        raise RuntimeError(
            "Restore provider or Compose destination differs from the existing installation.",
            suggestion="Use --root to recover the archive into a separate empty directory.",
        )
    if current.provider.type in FILE_PROVIDER_TYPES:
        if provider_local_path(root, current) != provider_local_path(root, manifest.config):
            raise RuntimeError("Restore provider destination differs from the selected context.")
    elif current.provider != manifest.config.provider:
        raise RuntimeError(
            "External provider destination or schema differs from the selected context.",
            suggestion=(
                "Protect and select the intended database explicitly; "
                "restore never adopts a database destination from an archive."
            ),
        )
    for relative in entries:
        target = check_managed_path(root, relative)
        original = source / relative
        if target.exists():
            check_source_tree(target)
            if target.is_dir() != original.is_dir():
                raise RuntimeError("Restore destination has a conflicting file or directory.")
        for child in original.rglob("*") if original.is_dir() else ():
            destination = check_managed_path(root, child.relative_to(source).as_posix())
            if destination.exists() and destination.is_dir() != child.is_dir():
                raise RuntimeError("Restore destination has a conflicting file or directory.")
    try:
        require_configured_schema(provider_from_config(root, current))
    except (OSError, ValueError, SFTPWardenError):
        raise DamagedTargetError(
            "The existing provider is damaged; recover into a separate empty directory with --root."
        ) from None
    if current.provider.type == ProviderType.SQLITE:
        provider = provider_local_path(root, current)
        if any(provider.with_name(provider.name + suffix).exists() for suffix in ("-wal", "-shm")):
            raise RuntimeError(
                "Close external SQLite connections before replacing the provider database."
            )
    for name in [*entries, JOURNAL]:
        parent = (root / name).parent
        while not parent.exists():
            parent = parent.parent
        if not os.access(parent, os.W_OK | os.X_OK):
            raise RuntimeError("Restore destination is not writable by this operator.")
    recovery_metadata(source, manifest, entries)
    return root


def verify_safety_archive(path: Path) -> ValidatedBackup:
    """Read the complete recovery point before trusting it to protect replacement."""
    with tempfile.TemporaryDirectory(prefix="sftpwarden-safety-check-") as directory:
        root = Path(directory)
        with tarfile.open(path, "r:gz") as archive:
            headers = extract_private_archive(archive, root, private_staging=True)
        return validate_backup_manifest(root, headers)


def publish_existing_files(
    root: Path,
    source: Path,
    manifest: ValidatedBackup,
    entries: list[str],
    safety: Path,
    *,
    runtime: RuntimeCapture | None = None,
    replace_provider: Callable[[], None] | None = None,
) -> None:
    """Publish each file atomically, preserving unrelated content and durable progress."""
    owner = project_owner(root)
    metadata = recovery_metadata(source, manifest, entries)
    if metadata:
        write_recovery_record(
            source,
            metadata,
            manifest.config,
            requires_recreate=runtime is not None,
        )
    files: list[str] = []
    directories: set[str] = set()
    for entry in entries:
        original = source / entry
        if original.is_dir():
            directories.add(entry)
            for child in original.rglob("*"):
                name = child.relative_to(source).as_posix()
                if child.is_dir():
                    directories.add(name)
                else:
                    files.append(name)
        else:
            files.append(entry)
    # Recovery metadata is part of state, even when the source selects only state.json.
    if metadata and "state/restore-metadata.json" not in files:
        files.append("state/restore-metadata.json")
    pending = sorted(set(files))
    if replace_provider is not None:
        pending.insert(0, PROVIDER_STEP)
    completed: list[str] = []
    journal = {
        "format": 1,
        "phase": "replacing_files",
        "safety_backup": str(safety.resolve()),
        "completed": completed,
        "pending": pending,
        "external_provider": replace_provider is not None,
        "recovery_action": (
            EXTERNAL_RECOVERY
            if replace_provider is not None
            else "Restore safety_backup into a separate empty target using --root."
        ),
    }

    def checkpoint() -> None:
        check_managed_path(root, JOURNAL)
        parent = (root / JOURNAL).parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if owner is not None:
            os.chown(parent, *owner, follow_symlinks=False)
        atomic_bytes(root / JOURNAL, json.dumps(journal, sort_keys=True).encode(), owner=owner)

    checkpoint()
    try:
        if runtime is not None:
            # Disable resumption before the first live effect, including the blocker.
            runtime.resume_on_exit = False
            blocker = check_managed_path(root, f"state/{BLOCK_FILE}")
            atomic_bytes(blocker, b'{"format":1,"phase":"replacing_files"}')
        for name in sorted(directories, key=lambda value: (len(Path(value).parts), value)):
            directory = check_managed_path(root, name)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if owner is not None and Path(name).parts[0] not in {"data", "state", "host_keys"}:
                os.chown(directory, *owner, follow_symlinks=False)
        while pending:
            name = pending[0]
            if name == PROVIDER_STEP and replace_provider is not None:
                replace_provider()
            else:
                target = check_managed_path(root, name)
                file_owner = (
                    owner if Path(name).parts[0] not in {"data", "state", "host_keys"} else None
                )
                with (
                    atomic_binary_output(target, owner=file_owner) as output,
                    (source / name).open("rb") as original_file,
                ):
                    shutil.copyfileobj(original_file, output)
            completed.append(name)
            pending.pop(0)
            checkpoint()
        journal["phase"] = "files_restored"
        checkpoint()
        if runtime is not None:
            check_managed_path(root, f"state/{BLOCK_FILE}").unlink()
            sync_directory(root / "state")
    except Exception as exc:
        # The last durable checkpoint deliberately treats an interrupted replacement
        # as pending, even if its rename succeeded. Never claim a rolled-back target.
        recovery_action = (
            EXTERNAL_RECOVERY
            if replace_provider is not None
            else f"Recover {safety.resolve()} into a separate empty directory with --root."
        )
        raise RuntimeError(
            "Restore interrupted; files or provider data may be partially restored.",
            suggestion=f"Keep it stopped. Inspect {root / JOURNAL}. {recovery_action}",
        ) from exc


def replace_external_provider(
    provider: BaseProvider,
    expected: ProviderUsers,
    replacement: ProviderUsers,
) -> None:
    """Use the destination's checked transaction and verify complete policy afterward."""
    provider.replace_if_unchanged(expected, replacement)
    if require_configured_schema(provider) != replacement:
        raise RuntimeError("External provider changed before restore verification completed.")


def inspect_runtime_destination(
    runtime: RuntimeCapture,
    entry: ContextEntry,
    manifest: ValidatedBackup,
    source: Path,
    entries: list[str],
) -> None:
    """Establish that host replacement addresses exactly the stopped runtime's mounts."""
    current = load_config(entry.config)
    root = Path(entry.root)
    if current != runtime.config:
        raise RuntimeError("Deploy pending configuration changes before an in-place restore.")
    if manifest.config.server != current.server:
        raise RuntimeError("Restore runtime destination differs; use a separate empty target.")
    mounts = {mount["Destination"]: mount for mount in runtime.info.get("Mounts", [])}
    paths = {
        current.server.state_dir: root / "state",
        current.server.host_keys_dir: root / "host_keys",
        current.server.data_dir: root / "data",
    }
    if current.provider.type in FILE_PROVIDER_TYPES:
        paths[str(PurePosixPath(current.provider.path).parent)] = provider_local_path(
            root, current
        ).parent
    for destination, host in paths.items():
        mount = mounts.get(destination)
        if (
            mount is None
            or mount.get("Type") != "bind"
            or Path(mount["Source"]).resolve() != host.resolve()
        ):
            raise RuntimeError("Restore runtime mount destination differs from the local project.")
    compose = check_managed_path(root, current.docker.compose_file)
    if yaml.safe_load(compose.read_text(encoding="utf-8")) != compose_model(
        current,
        root,
        allow_local_build=False,
    ):
        raise RuntimeError(
            "In-place runtime restore requires the generated Compose configuration.",
            suggestion="Preserve customized Compose files and recover into a separate --root.",
        )
    if not {"state", "host_keys"}.issubset(entries):
        raise RuntimeError("Runtime restore requires complete state and host-key archive entries.")
    if not any(item == "data" or item.startswith("data/") for item in entries):
        before = RuntimeState.load(root / "state" / "state.json")
        after = RuntimeState.load(source / "state" / "state.json")
        for name, identity in before.users.items():
            restored = after.users.get(name)
            if restored is None or (restored.uid, restored.gid) != (identity.uid, identity.gid):
                raise RuntimeError(
                    "Restore would change retained identities for data that is not being restored."
                )


def restore_existing_files(
    entry: ContextEntry,
    source: Path,
    manifest: ValidatedBackup,
    entries: list[str],
    *,
    include_data: bool,
    dry_run: bool,
    maintenance: bool,
) -> tuple[Path, Path]:
    """Protect an inspected destination before publishing restored files or provider policy."""
    from sftpwarden.services.backup import create_backup, default_backup_path

    root = inspect_existing_target(entry, source, manifest, entries)
    owner = project_owner(root)
    safety = default_backup_path("pre-restore").absolute()
    if maintenance:
        # Inspection is read-only with respect to the project and runtime. Lock only
        # the mutating route; dry-run does not create a project lock or safety archive.
        if dry_run:
            with inspect_runtime(entry) as runtime:
                inspect_runtime_destination(runtime, entry, manifest, source, entries)
            return root, safety
        with state_transaction(Path(entry.config), owner=owner), inspect_runtime(entry) as runtime:
            inspect_existing_target(entry, source, manifest, entries)
            inspect_runtime_destination(runtime, entry, manifest, source, entries)
            with stopped_runtime(runtime):
                with atomic_binary_output(safety, owner=owner) as output:
                    capture_runtime(runtime, output, include_data=include_data)
                protected = verify_safety_archive(safety)
                current = load_config(entry.config)
                lock = (
                    file_lock(provider_local_path(root, current), owner=owner)
                    if current.provider.type in FILE_PROVIDER_TYPES
                    else nullcontext()
                )
                with lock:
                    inspect_existing_target(entry, source, manifest, entries)
                    provider = provider_from_config(root, load_config(entry.config))
                    if require_configured_schema(provider) != protected.users:
                        raise RuntimeError(
                            "Provider changed after the safety backup; retry restore."
                        )
                    publish_existing_files(
                        root,
                        source,
                        manifest,
                        entries,
                        safety,
                        runtime=runtime,
                        replace_provider=(
                            None
                            if current.provider.type in FILE_PROVIDER_TYPES
                            else lambda: replace_external_provider(
                                provider, protected.users, manifest.users
                            )
                        ),
                    )
        return root, safety
    if dry_run:
        # Backup planning also checks maintenance prerequisites without writing.
        create_backup(
            context_name=entry.name, output=str(safety), include_data=include_data, dry_run=True
        )
        return root, safety
    with state_transaction(Path(entry.config), owner=owner):
        inspect_existing_target(entry, source, manifest, entries)
        create_backup(context_name=entry.name, output=str(safety), include_data=include_data)
        protected = verify_safety_archive(safety)
        config = load_config(entry.config)
        lock = (
            file_lock(provider_local_path(root, config), owner=owner)
            if config.provider.type in FILE_PROVIDER_TYPES
            else nullcontext()
        )
        with lock:
            inspect_existing_target(entry, source, manifest, entries)
            provider = provider_from_config(root, load_config(entry.config))
            if require_configured_schema(provider) != protected.users:
                raise RuntimeError("Provider changed after the safety backup; retry restore.")
            publish_existing_files(
                root,
                source,
                manifest,
                entries,
                safety,
                replace_provider=(
                    None
                    if config.provider.type in FILE_PROVIDER_TYPES
                    else lambda: replace_external_provider(
                        provider, protected.users, manifest.users
                    )
                ),
            )
    return root, safety


def project_owner(root: Path) -> tuple[int, int] | None:
    """Preserve operator-owned project files when an administrator restores runtime data."""
    if os.name == "posix" and os.geteuid() == 0:
        info = root.stat()
        return info.st_uid, info.st_gid
    return None
