"""Isolated Docker metadata probes with explicit ownership and cleanup."""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import uuid4

from sftpwarden.utils.errors import RuntimeError

VERSION_PROBE = [
    "python",
    "-c",
    "import importlib.metadata; print(importlib.metadata.version('sftpwarden'))",
]


def probe_version(execute: Callable[[list[str]], str], image: str) -> str:
    """Remove the probe and its anonymous volumes even after a client timeout."""
    nonce = uuid4().hex
    label = f"sftpwarden.io/version-probe={nonce}"
    try:
        return execute(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                f"sftpwarden-version-{nonce}",
                "--label",
                label,
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                VERSION_PROBE[0],
                image,
                *VERSION_PROBE[1:],
            ]
        )
    finally:
        owned = execute(
            ["docker", "container", "ls", "-aq", "--filter", f"label={label}"]
        ).splitlines()
        if any(not re.fullmatch(r"[0-9a-f]{12,64}", item) for item in owned):
            raise RuntimeError("Version-probe cleanup ownership could not be verified.")
        if owned:
            execute(["docker", "rm", "-f", "-v", *owned])
