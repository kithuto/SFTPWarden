"""Preflight the regular-file/directory backup subset before private extraction."""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from sftpwarden.utils.errors import RuntimeError

MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_BYTES = 100 * 1024**3
MAX_METADATA_BYTES = 64 * 1024**2
_WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{suffix}" for prefix in ("COM", "LPT") for suffix in "123456789¹²³"
}


def archive_path_key(path: PurePosixPath) -> str:
    """Use one collision rule on case-sensitive and case-insensitive filesystems."""
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def archive_relative_path(name: str) -> PurePosixPath:
    """Reject ambiguous host-dependent paths as well as traversal."""
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or PureWindowsPath(name).drive
        or "\\" in name
        or ":" in name
        or "\0" in name
    ):
        raise RuntimeError("Refusing unsafe backup member path.")
    for part in path.parts:
        if (
            part.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>"|?*' for char in part)
            or part.split(".", 1)[0].upper() in _WINDOWS_DEVICES
        ):
            raise RuntimeError("Refusing unsafe non-portable backup member path.")
    return path


def check_source_tree(path: Path) -> None:
    """Reject links and special files before an archive can be published."""
    info = path.lstat()
    if getattr(info, "st_reparse_tag", 0) in {0xA0000003, 0xA000000C}:
        raise RuntimeError("Refusing unsupported backup source link or junction.")
    if stat.S_ISDIR(info.st_mode):
        for child in path.iterdir():
            check_source_tree(child)
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise RuntimeError(
            "Refusing unsupported backup source: only regular files and directories."
        )


def _reject_destination_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in {
        0xA0000003,
        0xA000000C,
    }:
        raise RuntimeError("Refusing unsafe extraction destination link or junction.")


def check_managed_path(root: Path, relative: str) -> Path:
    """Reject links in the selected project-relative path, including its parents."""
    current = root
    _reject_destination_link(current)
    for part in archive_relative_path(relative).parts:
        current = current / part
        _reject_destination_link(current)
    return current


def extract_private_archive(
    archive: tarfile.TarFile,
    destination: Path,
    *,
    private_staging: bool = False,
) -> dict[str, tarfile.TarInfo]:
    """Validate all member headers and existing destination paths before writing."""
    members: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    names: dict[str, bool] = {}
    total = 0
    for member in archive:
        relative = archive_relative_path(member.name)
        normalized = archive_path_key(relative)
        if normalized in names:
            raise RuntimeError("Refusing duplicate backup member.")
        if not member.isfile() and not member.isdir():
            raise RuntimeError("Refusing unsupported backup member.")
        if member.size < 0 or member.mode & 0o7000 or min(member.uid, member.gid) < 0:
            raise RuntimeError("Refusing unsafe backup member metadata.")
        if relative.as_posix() in {"manifest.json", "provider/users.json"} and (
            member.size > MAX_METADATA_BYTES
        ):
            raise RuntimeError("Backup recovery metadata exceeds the supported limits.")
        total += member.size
        if len(members) >= MAX_ARCHIVE_MEMBERS or total > MAX_ARCHIVE_BYTES:
            raise RuntimeError("Backup exceeds the supported extraction limits.")
        names[normalized] = member.isdir()
        members.append((member, relative))
    _reject_destination_link(destination)
    for member, relative in members:
        for parent in relative.parents:
            key = archive_path_key(parent)
            if key in names and not names[key]:
                raise RuntimeError("Refusing unsafe backup file/directory collision.")
        target = destination.joinpath(*relative.parts)
        for path in (target, *target.parents):
            _reject_destination_link(path)
            if path == destination:
                break
        if target.exists() and not (member.isdir() and target.is_dir()):
            raise RuntimeError("Refusing to overwrite an existing extraction destination.")
    ancestor = destination
    while not ancestor.exists():
        ancestor = ancestor.parent
    if total > shutil.disk_usage(ancestor).free:
        raise RuntimeError("Insufficient space to extract the backup.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for member, relative in members:
        target = destination.joinpath(*relative.parts)
        if member.isdir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("Cannot read backup member.")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            target,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with source, os.fdopen(descriptor, "wb") as handle:
            shutil.copyfileobj(source, handle)
        target.chmod((member.mode & 0o777) | (0o600 if private_staging else 0))
    # Apply directory modes last so restrictive source directories do not prevent
    # extraction. The enclosing staging directory remains private (0700).
    for member, relative in sorted(members, key=lambda item: len(item[1].parts), reverse=True):
        if member.isdir():
            destination.joinpath(*relative.parts).chmod(
                (member.mode & 0o777) | (0o700 if private_staging else 0)
            )
    return {relative.as_posix(): member for member, relative in members}
