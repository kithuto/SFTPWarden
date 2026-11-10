from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from sftpwarden.utils.persistence import atomic_bytes


def chmod_private(path: str | Path) -> None:
    """Set private owner-only permissions on a file.

    Parameters
    ----------
    path
        File path whose permissions should be changed to ``0600``.
    """
    with suppress(OSError):
        os.chmod(path, 0o600)


def write_private_text(path: str | Path, text: str) -> None:
    """Publish complete UTF-8 text from a private same-directory temporary file.

    Parameters
    ----------
    path
        Destination file path.
    text
        UTF-8 text to write.
    """
    atomic_bytes(Path(path), text.encode("utf-8"))
