"""Preserve readable damaged project material without claiming a complete recovery point."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
from pathlib import Path

from sftpwarden.utils.archives import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_METADATA_BYTES,
    archive_relative_path,
    check_managed_path,
)
from sftpwarden.utils.errors import RuntimeError, SFTPWardenError
from sftpwarden.utils.persistence import atomic_binary_output


class DamagedTargetError(RuntimeError):
    """An explicitly inspected configuration/provider is damaged, not an empty target."""


def preserve_readable_project(root: Path, entries: list[str], output: Path) -> Path:
    """Capture bounded readable regular files and inventory omissions, never follow links."""
    # The archive must not become a growing member of the source being preserved.
    if output.resolve().is_relative_to(root.resolve()):
        raise RuntimeError(
            "Damaged-target preservation must be written outside the original project."
        )
    inventory: dict[str, dict[str, object]] = {}
    total = 0
    seen: set[str] = set()
    pending = list(reversed(entries))
    with tempfile.TemporaryDirectory(prefix="sftpwarden-preservation-") as directory:
        copied = Path(directory) / "member"
        with (
            atomic_binary_output(output) as target,
            tarfile.open(fileobj=target, mode="w:gz") as archive,
        ):
            while pending:
                name = pending.pop()
                if name in seen:
                    continue
                seen.add(name)
                if len(seen) >= MAX_ARCHIVE_MEMBERS:
                    inventory["limit"] = {
                        "status": "inventory_limit",
                        "unvisited": len(pending) + 1,
                    }
                    break
                try:
                    relative = archive_relative_path(name)
                    if relative.as_posix() == "preservation.json":
                        raise ValueError
                    path = check_managed_path(root, relative.as_posix())
                    info = path.lstat()
                except FileNotFoundError:
                    inventory[name] = {"status": "missing"}
                    continue
                except (OSError, SFTPWardenError, ValueError):
                    inventory[name] = {"status": "unreadable_or_unsafe_path"}
                    continue
                details: dict[str, object] = {
                    "uid": info.st_uid,
                    "gid": info.st_gid,
                    "mode": stat.S_IMODE(info.st_mode),
                }
                inventory[name] = details
                if stat.S_ISDIR(info.st_mode):
                    try:
                        children = list(path.iterdir())
                    except OSError:
                        details["status"] = "unreadable_directory"
                        continue
                    details["status"] = "directory"
                    pending.extend(
                        child.relative_to(root).as_posix()
                        for child in sorted(children, reverse=True)
                    )
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o700
                    archive.addfile(member)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    details["status"] = "unsupported_member"
                    continue
                if total + info.st_size > MAX_ARCHIVE_BYTES:
                    details["status"] = "size_limit"
                    continue
                try:
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    with os.fdopen(descriptor, "rb") as source, copied.open("wb") as snapshot:
                        before = os.fstat(source.fileno())
                        if (before.st_dev, before.st_ino) != (
                            info.st_dev,
                            info.st_ino,
                        ) or not stat.S_ISREG(before.st_mode):
                            raise ValueError
                        digest = hashlib.sha256()
                        count = 0
                        while chunk := source.read(1024 * 1024):
                            count += len(chunk)
                            if count > info.st_size:
                                raise ValueError
                            digest.update(chunk)
                            snapshot.write(chunk)
                        after = os.fstat(source.fileno())
                        if count != info.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        ):
                            raise ValueError
                except (OSError, ValueError):
                    details["status"] = "unreadable_or_changed_file"
                    copied.unlink(missing_ok=True)
                    continue
                member = tarfile.TarInfo(name)
                member.mode = 0o600
                member.size = info.st_size
                with copied.open("rb") as snapshot:
                    archive.addfile(member, snapshot)
                total += info.st_size
                details.update(
                    {"status": "preserved", "size": info.st_size, "sha256": digest.hexdigest()}
                )
                copied.unlink()
            record = json.dumps(
                {
                    "format": 1,
                    "purpose": "damaged-target-preservation",
                    "complete_backup": False,
                    "consistency": "not-established",
                    "members": inventory,
                    "recovery": (
                        "Retain the original installation. Restore a separately validated "
                        "backup into a new empty target."
                    ),
                },
                sort_keys=True,
            ).encode()
            if len(record) > MAX_METADATA_BYTES:
                raise RuntimeError(
                    "The preservation inventory exceeds its supported bound; the "
                    "original was retained."
                )
            member = tarfile.TarInfo("preservation.json")
            member.mode = 0o600
            member.size = len(record)
            archive.addfile(member, io.BytesIO(record))
    return output
