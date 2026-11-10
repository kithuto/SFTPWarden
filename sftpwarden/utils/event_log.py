"""Small service-event output with explicit settings and no exception serialization."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Literal

from sftpwarden.config import LoggingConfig

Level = Literal["debug", "info", "warning", "error"]
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


def service_event(
    settings: LoggingConfig,
    level: Level,
    message: str,
    *,
    context: str | None = None,
) -> None:
    """Emit only caller-selected public event text; callers must not pass exception payloads."""
    if _LEVELS[level] < _LEVELS[settings.level]:
        return
    record = {
        "time": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message,
    }
    if context is not None:
        record["context"] = context
    if settings.format == "json":
        print(json.dumps(record, ensure_ascii=True), file=sys.stderr, flush=True)
    else:
        suffix = f" [{context}]" if context else ""
        print(f"{level.upper()}{suffix}: {message}", file=sys.stderr, flush=True)
