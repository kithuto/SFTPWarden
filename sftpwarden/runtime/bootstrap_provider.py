"""Validate and atomically publish declarative Kubernetes input inside the Linux init Pod."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, ProviderType, SFTPWardenConfig, load_config
from sftpwarden.providers import provider_for_config
from sftpwarden.providers.documents import parse_provider_document
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.runtime.filesystem import open_private_directory
from sftpwarden.utils.archives import MAX_METADATA_BYTES
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes, reject_symlink


def bootstrap_provider(config: SFTPWardenConfig, source_directory: Path) -> None:
    """Keep existing SQLite PVCs; publish validated YAML/CSV without partial copies."""
    if config.provider.type not in FILE_PROVIDER_TYPES:
        return
    path = Path(config.provider.path)
    if path.parent != Path("/etc/sftpwarden/provider-data"):
        raise RuntimeError("Kubernetes provider must use the dedicated provider directory.")
    content = None
    if config.provider.type != ProviderType.SQLITE:
        source = source_directory / path.name
        with source.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Bootstrap provider must be a regular document.")
            content = stream.read(MAX_METADATA_BYTES + 1)
        if len(content) > MAX_METADATA_BYTES:
            raise RuntimeError("Bootstrap provider exceeds the supported document size.")
        users = parse_provider_document(
            content.decode("utf-8"), "csv" if config.provider.type == ProviderType.CSV else "yaml"
        )
        if users.schema_version != config.provider.user_schema:
            raise RuntimeError(
                "Bootstrap provider schema does not match the runtime configuration."
            )
    descriptor = open_private_directory(path.parent)
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o700)
        destination = Path(f"/proc/self/fd/{descriptor}") / path.name
        reject_symlink(destination)
        if content is not None:
            atomic_bytes(destination, content)
        else:
            info = destination.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError("SQLite provider must be a regular file with one link.")
            for suffix in ("-wal", "-shm", "-journal"):
                reject_symlink(destination.with_name(destination.name + suffix))
            # No empty SQLite placeholder: initialize/restore this PVC explicitly.
            require_configured_schema(provider_for_config(config.provider, destination))
        file_descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(file_descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError("Provider storage must be a regular file.")
            os.fchown(file_descriptor, 0, 0)
            os.fchmod(file_descriptor, 0o600)
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> None:
    """Initialize cluster provider storage with controlled error output."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        bootstrap_provider(load_config(args.config), args.config.parent)
    except Exception:
        raise SystemExit(
            "Provider bootstrap failed. Check its source and schema; SQLite requires "
            "an initialized provider PVC."
        ) from None


if __name__ == "__main__":
    main()
