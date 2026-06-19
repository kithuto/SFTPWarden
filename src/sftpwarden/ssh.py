from __future__ import annotations


def uses_default_ssh_identity(ssh_key: str | None) -> bool:
    return ssh_key is None or ssh_key.strip().lower() == "default"
