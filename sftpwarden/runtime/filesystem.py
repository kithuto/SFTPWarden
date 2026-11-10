"""Descriptor-relative filesystem changes for the privileged Linux runtime."""

from __future__ import annotations

import os
from contextlib import ExitStack, suppress
from pathlib import Path


def open_private_directory(directory: Path) -> int:
    """Open a dedicated Linux directory through root-owned, non-writable ancestors."""
    if not directory.is_absolute() or ".." in directory.parts or directory == Path("/"):
        raise ValueError("Managed private directory must be an absolute dedicated path.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for part in directory.parts[1:]:
            parent = os.fstat(descriptor)
            if parent.st_uid != 0 or parent.st_mode & 0o022:
                raise ValueError(
                    "Managed private directory ancestors must be root-owned and not "
                    "writable by other users."
                )
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ensure_upload_tree(
    data_root: Path,
    username: str,
    upload_dir: str,
    *,
    uid: int,
    gid: int,
    root_mode: int,
    upload_mode: int,
) -> None:
    """Create a chroot tree without following user-controlled directory links.

    The configured data root is the trust anchor. Descendants are opened relative
    to already-open directory descriptors. Replacing a pathname with a symlink
    cannot redirect subsequent ownership or permission operations.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    data_root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        data_fd = os.open(data_root, flags)
        stack.callback(os.close, data_fd)
        os.fchown(data_fd, 0, 0)
        os.fchmod(data_fd, root_mode)

        def child(parent: int, name: str) -> int:
            with suppress(FileExistsError):
                os.mkdir(name, mode=root_mode, dir_fd=parent)
            descriptor = os.open(name, flags, dir_fd=parent)
            stack.callback(os.close, descriptor)
            return descriptor

        root_fd = child(data_fd, username)
        os.fchown(root_fd, 0, 0)
        os.fchmod(root_fd, root_mode)
        current = root_fd
        for component in Path(upload_dir).parts:
            # Model validation already enforces this; retain the privileged boundary.
            if component in {"", ".", "..", "/"}:
                raise ValueError("Upload directory must remain inside the user chroot.")
            current = child(current, component)
        if current == root_fd:
            raise ValueError("Upload directory must be below the user chroot.")
        os.fchown(current, uid, gid)
        os.fchmod(current, upload_mode)
