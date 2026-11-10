"""Identify official managed repositories without matching lookalike registries."""

from __future__ import annotations


def official_image(image: str, repository: str = "ghcr.io/kithuto/sftpwarden") -> bool:
    """Compare the complete repository after removing a tag or digest."""
    name = image.split("@", 1)[0]
    if ":" in name.rsplit("/", 1)[-1]:
        name = name.rsplit(":", 1)[0]
    return name == repository
