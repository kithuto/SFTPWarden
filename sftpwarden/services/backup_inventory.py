"""Record the exact bytes and portable metadata written to a recovery archive."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

from sftpwarden.utils.archives import archive_path_key, archive_relative_path
from sftpwarden.utils.errors import RuntimeError

Inventory = dict[str, dict[str, str | int]]
OwnershipReader = Callable[[Path, str], tuple[int, int]]


class _DigestReader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        self.digest.update(chunk)
        return chunk


def member_metadata(member: tarfile.TarInfo, digest: str = "") -> dict[str, str | int]:
    """Describe the tar metadata without owner names or secret values."""
    return {
        "type": "directory" if member.isdir() else "file",
        "size": member.size,
        "mode": member.mode & 0o7777,
        "uid": member.uid,
        "gid": member.gid,
        "sha256": digest,
    }


def add_source(
    archive: tarfile.TarFile,
    source: Path,
    name: str,
    inventory: Inventory,
    *,
    seen: set[str] | None = None,
    ownership: OwnershipReader | None = None,
) -> None:
    """Archive regular files/directories and hash bytes actually read by tarfile."""
    path = archive_relative_path(name)
    key = archive_path_key(path)
    if seen is None:
        seen = {archive_path_key(archive_relative_path(item)) for item in inventory}
    if key in seen or key in {
        "manifest.json",
        "provider/users.json",
    }:
        raise RuntimeError("Refusing duplicate backup member.")
    seen.add(key)
    info = source.lstat()
    if info.st_mode & 0o7000:
        raise RuntimeError("Refusing unsafe backup member permissions.")
    if stat.S_ISDIR(info.st_mode):
        member = archive.gettarinfo(str(source), arcname=path.as_posix())
        if not member.isdir():
            raise RuntimeError("Backup source changed during capture.")
        if ownership is not None:
            member.uid, member.gid = ownership(source, path.as_posix())
        archive.addfile(member)
        inventory[path.as_posix()] = member_metadata(member)
        for child in sorted(source.iterdir()):
            add_source(
                archive,
                child,
                f"{path.as_posix()}/{child.name}",
                inventory,
                seen=seen,
                ownership=ownership,
            )
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise RuntimeError("Refusing unsupported backup source.")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError("Backup source changed during capture.")
        member = archive.gettarinfo(str(source), arcname=path.as_posix(), fileobj=handle)
        if not member.isfile():
            raise RuntimeError("Refusing unsupported backup source.")
        if ownership is not None:
            member.uid, member.gid = ownership(source, path.as_posix())
        reader = _DigestReader(handle)
        archive.addfile(member, cast(BinaryIO, reader))
        after = os.fstat(handle.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise RuntimeError("Backup source changed during capture.")
        inventory[path.as_posix()] = member_metadata(member, reader.digest.hexdigest())


def add_payload(
    archive: tarfile.TarFile,
    name: str,
    payload: bytes,
    inventory: Inventory,
) -> None:
    """Add private generated recovery material to the inventory."""
    info = tarfile.TarInfo(name)
    info.mode = 0o600
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))
    inventory[name] = member_metadata(info, hashlib.sha256(payload).hexdigest())


def validate_inventory(
    root: Path,
    raw: object,
    headers: dict[str, tarfile.TarInfo],
) -> None:
    """Compare manifest claims with every extracted member, including modes and owners."""
    if not isinstance(raw, dict) or set(raw) != set(headers) - {"manifest.json"}:
        raise RuntimeError("Backup integrity inventory does not match its archive members.")
    for name, expected in raw.items():
        member = headers[name]
        digest = ""
        if member.isfile():
            with (root / name).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected != member_metadata(member, digest):
            raise RuntimeError("Backup member integrity verification failed.")
