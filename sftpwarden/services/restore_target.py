"""Stage file recovery into a verified empty, explicitly selected local target."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, DeployTarget
from sftpwarden.runtime.core import RuntimeState
from sftpwarden.runtime.recovery import STATUS_FILE, validated_metadata, write_recovery_record
from sftpwarden.services.backup_inventory import Inventory
from sftpwarden.services.backup_manifest import ValidatedBackup
from sftpwarden.utils.archives import check_managed_path
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.paths import expand_path
from sftpwarden.utils.persistence import sync_directory


def inspect_empty_target(
    value: str,
    manifest: ValidatedBackup,
    *,
    source: Path,
    entries: list[str],
    protected_external: bool = False,
) -> Path:
    """Inspect the file target without assuming isolation of an external database."""
    requested = expand_path(value).absolute()
    root = requested.parent.resolve() / requested.name
    if manifest.config.provider.type not in FILE_PROVIDER_TYPES and not protected_external:
        raise RuntimeError(
            "An empty directory is not a verified isolated external provider destination.",
            suggestion="Select and protect a separate database before restoring provider contents.",
        )
    if manifest.config.deploy.target != DeployTarget.COMPOSE or manifest.config.remote.enabled:
        raise RuntimeError("--root currently requires a local Compose recovery archive.")
    if not root.parent.is_dir():
        raise RuntimeError("Create the recovery destination's parent directory first.")
    require_empty_directory(root)
    recovery_metadata(source, manifest, entries)
    return root


def recovery_metadata(source: Path, manifest: ValidatedBackup, entries: list[str]) -> Inventory:
    """Preflight ownership for both a dry run and publication into a new target."""
    runtime_entries = [
        entry for entry in entries if entry.split("/", 1)[0] in {"state", "host_keys", "data"}
    ]
    has_material = any(
        (source / entry).is_file() or any(path.is_file() for path in (source / entry).rglob("*"))
        for entry in runtime_entries
    )
    if not has_material:
        return {}
    selected = {
        name: item
        for name, item in manifest.members.items()
        if name != f"state/{STATUS_FILE}"
        and any(name == entry or name.startswith(entry + "/") for entry in runtime_entries)
    }
    if "state/state.json" not in selected:
        raise RuntimeError(
            "Fresh runtime recovery requires metadata and retained identity state in the backup."
        )
    state = RuntimeState.load(source / "state" / "state.json")
    validated_metadata(selected, state, manifest.config)
    return selected


def require_empty_directory(root: Path) -> None:
    """Refuse unknown or conflicting content, without treating access errors as absence."""
    check_managed_path(root.parent, root.name)
    try:
        root.lstat()
    except FileNotFoundError:
        return
    if not root.is_dir() or any(root.iterdir()):
        raise RuntimeError("Fresh recovery requires an empty destination directory.")


def publish_fresh_files(
    root: Path,
    source: Path,
    entries: list[str],
    manifest: ValidatedBackup,
    *,
    project_owner: tuple[int, int] | None = None,
) -> None:
    """Prepare all selected files privately before exposing the recovered project."""
    require_empty_directory(root)
    selected = recovery_metadata(source, manifest, entries)
    with tempfile.TemporaryDirectory(prefix=".sftpwarden-recovery-", dir=root.parent) as temporary:
        staged = Path(temporary)
        for relative in entries:
            original = source / relative
            destination = staged / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if original.is_dir():
                shutil.copytree(original, destination)
            else:
                shutil.copy2(original, destination)
        if selected:
            write_recovery_record(staged, selected, manifest.config)
        if project_owner is not None:
            # Container-only caller. Never request Linux ownership from host workflows.
            for copied in [*staged.rglob("*"), staged]:
                os.chown(copied, *project_owner, follow_symlinks=False)
        for copied in staged.rglob("*"):
            if copied.is_file():
                with copied.open("r+b") as stream:
                    os.fsync(stream.fileno())
        for directory in sorted(
            (path for path in staged.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            sync_directory(directory)
        sync_directory(staged)
        # Reinspect after staging. rmdir cannot remove newly created content.
        require_empty_directory(root)
        if root.exists():
            root.rmdir()
        os.rename(staged, root)
        sync_directory(root.parent)
