"""Wake file-provider reconciliation without repeatedly parsing unchanged files."""

from __future__ import annotations

import time
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, SFTPWardenConfig

ProviderStamp = tuple[int, int, int] | None


def provider_stamp(config: SFTPWardenConfig) -> ProviderStamp:
    """Observe only the configured user file; failure is handled by reconciliation."""
    if not config.sync.enabled or config.provider.type not in FILE_PROVIDER_TYPES:
        return None
    try:
        info = Path(config.provider.path).stat()
    except OSError:
        return None
    return info.st_ino, info.st_size, info.st_mtime_ns


def wait_for_provider_change(
    config: SFTPWardenConfig, previous: ProviderStamp, seconds: float
) -> None:
    """Wait until the periodic deadline or an observed user-file change.

    Database providers keep their configured polling cadence. Periodic passes
    still enforce expiry and detect changes that file metadata cannot reveal.
    """
    if not config.sync.enabled or config.provider.type not in FILE_PROVIDER_TYPES:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    remaining = seconds
    while remaining > 0:
        if provider_stamp(config) != previous:
            return
        time.sleep(min(1.0, remaining))
        remaining = deadline - time.monotonic()
