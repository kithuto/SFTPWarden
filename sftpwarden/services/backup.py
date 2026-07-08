from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, load_config, provider_local_path
from sftpwarden.contexts import ContextEntry, resolve_context
from sftpwarden.providers import provider_from_config
from sftpwarden.remote.ssh import rsync_ssh_transport, ssh_base_command
from sftpwarden.services.context_cleanup import ensure_remote_only_root_available
from sftpwarden.services.provider_transfer import deserialize_users, serialize_users
from sftpwarden.system.commands import run_checked
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import ContextError, RuntimeError

BACKUP_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BackupResult:
    """Backup or restore result."""

    path: Path
    entries: list[str]
    safety_backup: Path | None = None


def create_backup(
    *,
    context_name: str | None,
    output: str | None,
    include_data: bool = False,
    dry_run: bool = False,
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
    entry = resolve_context(context_name=context_name)
    if not entry.root:
        return create_remote_backup(
            entry=entry,
            output=output,
            include_data=include_data,
            dry_run=dry_run,
        )
    root = Path(entry.root)
    config = load_config(root / "sftpwarden.yaml")
    output_path = Path(output) if output else default_backup_path(entry.name)
    entries = backup_entries(root, config, include_data=include_data)
    if dry_run:
        return BackupResult(path=output_path, entries=entries)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "sftpwarden_version": get_version(),
        "context": entry.name,
        "provider": config.provider.type.value,
        "include_data": include_data,
        "entries": entries,
    }
    with tarfile.open(output_path, "w:gz") as archive:
        add_json(archive, "manifest.json", manifest)
        for relative in entries:
            source = root / relative
            if source.exists():
                archive.add(source, arcname=relative)
        try:
            users = provider_from_config(root, config).read()
            add_text(archive, "provider/users.json", serialize_users(users, "json"))
        except Exception as exc:  # noqa: BLE001
            add_text(archive, "provider/users-error.txt", str(exc))
    return BackupResult(path=output_path, entries=entries)


def restore_backup(
    *,
    context_name: str | None,
    backup_path: str,
    include_data: bool = False,
    dry_run: bool = False,
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
    entry = resolve_context(context_name=context_name)
    root = Path(entry.root) if entry.root else None
    archive_path = Path(backup_path)
    if not archive_path.exists():
        raise RuntimeError(f"Backup file not found: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="sftpwarden-restore-") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as archive:
            safe_extract(archive, tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        entries = [
            entry
            for entry in manifest.get("entries", [])
            if include_data or not str(entry).startswith("data")
        ]
        safety = default_backup_path("pre-restore")
        if dry_run:
            return BackupResult(path=archive_path, entries=entries, safety_backup=safety)
        create_backup(context_name=context_name, output=str(safety), include_data=include_data)
        if root is None:
            restore_remote_backup(entry=entry, extracted=tmp_path, entries=entries)
            return BackupResult(path=archive_path, entries=entries, safety_backup=safety)
        for relative in entries:
            source = tmp_path / relative
            destination = root / relative
            if not source.exists():
                continue
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        users_json = tmp_path / "provider" / "users.json"
        config_path = root / "sftpwarden.yaml"
        if users_json.exists() and config_path.exists():
            config = load_config(config_path)
            if config.provider.type not in FILE_PROVIDER_TYPES:
                users = deserialize_users(users_json.read_text(encoding="utf-8"), "json")
                provider_from_config(root, config).write(users)
        return BackupResult(path=archive_path, entries=entries, safety_backup=safety)


def create_remote_backup(
    *,
    entry: ContextEntry,
    output: str | None,
    include_data: bool,
    dry_run: bool,
) -> BackupResult:
    """Create a backup from a remote-only context.

    Parameters
    ----------
    entry
        Remote context entry.
    output
        Optional output path.
    include_data
        Whether to include SFTP user data.
    dry_run
        Whether to avoid running remote commands.

    Returns
    -------
    BackupResult
        Backup result.
    """
    if not entry.remote:
        raise ContextError(
            f"Context {entry.name} has no local root or remote settings.",
            suggestion="Use a local, remote local-sync, or valid remote-only context.",
        )
    output_path = Path(output) if output else default_backup_path(entry.name)
    candidates = remote_backup_candidates(entry.remote.compose_file, include_data=include_data)
    if dry_run:
        return BackupResult(
            path=output_path,
            entries=[f"remote:{candidate}" for candidate in candidates],
        )
    ensure_remote_only_root_available(entry)

    with tempfile.TemporaryDirectory(prefix="sftpwarden-remote-backup-") as tmp:
        tmp_path = Path(tmp)
        raw_archive = tmp_path / "remote.tar.gz"
        remote_root = shlex.quote(entry.remote.remote_root)
        candidate_list = " ".join(shlex.quote(candidate) for candidate in candidates)
        remote_script = (
            f"cd {remote_root} && "
            "set --; "
            f'for p in {candidate_list}; do [ -e "$p" ] && set -- "$@" "$p"; done; '
            '[ "$#" -gt 0 ] && tar -czf - "$@"'
        )
        result = subprocess.run(  # noqa: S603
            [*ssh_base_command(entry.remote), remote_script],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Remote backup failed for context {entry.name}.",
                suggestion=result.stderr.decode("utf-8", errors="replace").strip()
                or "Verify SSH access and remote project files.",
            )
        raw_archive.write_bytes(result.stdout)
        raw_root = tmp_path / "remote"
        raw_root.mkdir()
        with tarfile.open(raw_archive, "r:gz") as archive:
            safe_extract(archive, raw_root)
        entries = backup_entries_from_root(
            raw_root,
            compose_file=entry.remote.compose_file,
            include_data=include_data,
        )
        write_backup_archive(
            output_path=output_path,
            root=raw_root,
            entries=entries,
            context_name=entry.name,
            provider_type=entry.provider.value,
            include_data=include_data,
        )
    return BackupResult(path=output_path, entries=entries)


def restore_remote_backup(*, entry: ContextEntry, extracted: Path, entries: list[str]) -> None:
    """Restore extracted backup files to a remote-only context.

    Parameters
    ----------
    entry
        Remote context entry.
    extracted
        Directory containing extracted backup files.
    entries
        Manifest entries to restore.
    """
    if not entry.remote:
        raise ContextError(f"Remote context {entry.name} is missing remote settings.")
    with tempfile.TemporaryDirectory(prefix="sftpwarden-remote-restore-") as tmp:
        upload_root = Path(tmp) / "upload"
        upload_root.mkdir()
        for relative in entries:
            source = extracted / relative
            if not source.exists():
                continue
            destination = upload_root / relative
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        remote_root = shlex.quote(entry.remote.remote_root)
        run_checked(
            [*ssh_base_command(entry.remote), f"mkdir -p {remote_root}"],
            error_type=RuntimeError,
            message=f"Remote restore failed for context {entry.name}.",
            fallback_suggestion="Verify SSH access and remote permissions.",
        )
        run_checked(
            [
                "rsync",
                "-az",
                "--protect-args",
                "-e",
                rsync_ssh_transport(entry.remote),
                f"{upload_root}/",
                f"{entry.remote.user}@{entry.remote.host}:{entry.remote.remote_root.rstrip('/')}/",
            ],
            error_type=RuntimeError,
            message=f"Remote restore failed for context {entry.name}.",
            fallback_suggestion="Verify SSH access and remote permissions.",
        )


def write_backup_archive(
    *,
    output_path: Path,
    root: Path,
    entries: list[str],
    context_name: str,
    provider_type: str,
    include_data: bool,
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "sftpwarden_version": get_version(),
        "context": context_name,
        "provider": provider_type,
        "include_data": include_data,
        "entries": entries,
    }
    with tarfile.open(output_path, "w:gz") as archive:
        add_json(archive, "manifest.json", manifest)
        for relative in entries:
            source = root / relative
            if source.exists():
                archive.add(source, arcname=relative)
        config_path = root / "sftpwarden.yaml"
        if config_path.exists():
            try:
                config = load_config(config_path)
                users = provider_from_config(root, config).read()
                add_text(archive, "provider/users.json", serialize_users(users, "json"))
            except Exception as exc:  # noqa: BLE001
                add_text(archive, "provider/users-error.txt", str(exc))


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


def backup_entries(root: Path, config, *, include_data: bool) -> list[str]:
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
    entries = ["sftpwarden.yaml", config.docker.compose_file]
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
    return Path(f"sftpwarden-{context_name}-{stamp}.tar.gz")


def add_json(archive: tarfile.TarFile, name: str, data: dict) -> None:
    """Add JSON data to an archive."""
    add_text(archive, name, json.dumps(data, indent=2, sort_keys=True) + "\n")


def add_text(archive: tarfile.TarFile, name: str, text: str) -> None:
    """Add text to an archive."""
    encoded = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(encoded)
    archive.addfile(info, fileobj=io.BytesIO(encoded))


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    """Extract a tar archive without allowing path traversal.

    Parameters
    ----------
    archive
        Archive to extract.
    destination
        Destination directory.
    """
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if root not in {target, *target.parents}:
            raise RuntimeError(f"Refusing unsafe backup member: {member.name}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise RuntimeError(f"Refusing unsupported backup member: {member.name}")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"Cannot read backup member: {member.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with source, target.open("wb") as handle:
            shutil.copyfileobj(source, handle)
