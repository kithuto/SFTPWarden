from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path


def chmod_private(path: str | Path) -> None:
    with suppress(OSError):
        os.chmod(path, 0o600)


def write_private_text(path: str | Path, text: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")
    chmod_private(file_path)
