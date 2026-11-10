"""Validate recovery inputs before inspecting or changing a restore destination."""

from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

from sftpwarden.config import (
    FILE_PROVIDER_TYPES,
    SFTPWardenConfig,
    load_config,
    provider_local_path,
)
from sftpwarden.providers import provider_from_config
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.services.backup_inventory import Inventory, validate_inventory
from sftpwarden.services.provider_transfer import deserialize_users
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.archives import archive_path_key, archive_relative_path
from sftpwarden.utils.errors import RuntimeError, SFTPWardenError


@dataclass(frozen=True)
class ValidatedBackup:
    """Recovery metadata whose selected files and provider policy are present."""

    entries: list[str]
    config_path: str
    config: SFTPWardenConfig
    users: ProviderUsers
    members: Inventory
    provider_destination: str | None = None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def validate_backup_manifest(
    root: Path,
    headers: dict[str, tarfile.TarInfo] | None = None,
) -> ValidatedBackup:
    """Read recovery material without trusting arbitrary copy destinations."""
    try:
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, ValueError):
        raise RuntimeError("Backup manifest is missing or invalid.") from None
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int:
        raise RuntimeError("Backup manifest must declare its format version.")
    if manifest["schema_version"] not in {1, 2}:
        raise RuntimeError("Unsupported backup manifest version.")
    destination = manifest.get("provider_destination")
    if destination is not None and (
        not isinstance(destination, str)
        or len(destination) != 64
        or any(character not in "0123456789abcdef" for character in destination)
    ):
        raise RuntimeError("Backup provider destination fingerprint is invalid.")
    if manifest["schema_version"] == 2:
        if headers is None:
            raise RuntimeError("Backup integrity verification requires the archive headers.")
        validate_inventory(root, manifest.get("members"), headers)
    entries = manifest.get("entries")
    if (
        not isinstance(entries, list)
        or not entries
        or any(not isinstance(item, str) for item in entries)
    ):
        raise RuntimeError("Backup manifest entries must be a nonempty list of paths.")
    normalized = [archive_relative_path(item).as_posix() for item in entries]
    keys = [archive_path_key(archive_relative_path(item)) for item in normalized]
    if len(set(keys)) != len(keys):
        raise RuntimeError("Backup manifest contains duplicate entries.")
    for item in normalized:
        for parent in archive_relative_path(item).parents:
            if archive_path_key(parent) in keys:
                raise RuntimeError("Backup manifest contains overlapping entries.")
    config_name = manifest.get("config_path", "sftpwarden.yaml")
    if not isinstance(config_name, str):
        raise RuntimeError("Backup config path must be a relative path.")
    config_relative = archive_relative_path(config_name)
    reserved = {"data", "host_keys", "state", "provider", ".git", ".sftpwarden"}
    if config_relative.parts[0] in reserved or config_relative.as_posix() not in normalized:
        raise RuntimeError("Backup configuration is absent or conflicts with managed storage.")
    for relative in normalized:
        if not (root / relative).exists():
            raise RuntimeError("Backup is missing a member declared in its manifest.")
    try:
        config = load_config(root / config_relative)
        snapshot = root / "provider" / "users.json"
        users = deserialize_users(snapshot.read_text(encoding="utf-8"), "json")
    except (OSError, ValueError, SFTPWardenError):
        raise RuntimeError(
            "Backup configuration or required provider snapshot is invalid."
        ) from None
    if users.schema_version != config.provider.user_schema:
        raise RuntimeError("Backup provider snapshot and configuration schemas do not match.")
    compose = archive_relative_path(config.docker.compose_file)
    if compose.parts[0] in reserved:
        raise RuntimeError("Backup Compose path conflicts with managed storage.")
    allowed_files = {config_relative.as_posix(), compose.as_posix()}
    if config.provider.type in FILE_PROVIDER_TYPES:
        provider_path = provider_local_path(root, config)
        provider_relative = provider_path.relative_to(root).as_posix()
        archive_relative_path(provider_relative)
        allowed_files.add(provider_relative)
        if provider_relative not in normalized:
            raise RuntimeError("Backup manifest omits the required provider file.")
        if require_configured_schema(provider_from_config(root, config)) != users:
            raise RuntimeError("Backup provider file and logical snapshot do not match.")
    for relative in normalized:
        if relative in allowed_files and not (root / relative).is_file():
            raise RuntimeError("Backup manifest requires a regular configuration/provider file.")
        if relative not in allowed_files and archive_relative_path(relative).parts[0] not in {
            "data",
            "host_keys",
            "state",
        }:
            raise RuntimeError("Backup manifest includes an unmanaged destination path.")
    return ValidatedBackup(
        entries=normalized,
        config_path=config_relative.as_posix(),
        config=config,
        users=users,
        members=manifest["members"] if manifest["schema_version"] == 2 else {},
        provider_destination=destination,
    )
