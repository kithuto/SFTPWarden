"""Bound synchronous psycopg socket waits without global callbacks or background threads."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator
from typing import Any, TypeVar, cast

from sftpwarden.utils.errors import ProviderError

_T = TypeVar("_T")
SOCKET_SECONDS = 20.0


def deadline_generator(
    generator: Generator[Any, Any, _T],
    *,
    deadline: float,
    close: Callable[[], None],
) -> Generator[Any, Any, _T]:
    """Check each readiness wakeup, including idle polling, and discard timed-out sessions."""
    try:
        readiness = next(generator)
        while True:
            if time.monotonic() >= deadline:
                close()
                raise TimeoutError("PostgreSQL socket operation exceeded its deadline.")
            ready = yield readiness
            if time.monotonic() >= deadline:
                close()
                raise TimeoutError("PostgreSQL socket operation exceeded its deadline.")
            readiness = generator.send(ready)
    except StopIteration as completed:
        return cast(_T, completed.value)
    finally:
        generator.close()


def connect_postgres(driver: Any, dsn: str, **kwargs: Any) -> Any:
    """Preserve native DSN/TLS parsing while bounding connect and subsequent socket waits."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        options = conninfo_to_dict(dsn)
        value = options.get("connect_timeout", os.environ.get("PGCONNECT_TIMEOUT", "5"))
        timeout = int(str(value))
        if str(timeout) != str(value) or not 1 <= timeout <= 30:
            raise ValueError
    except (ValueError, TypeError):
        raise ProviderError(
            "PostgreSQL connect_timeout must be an integer from 1 to 30 seconds."
        ) from None
    connection = driver.connect(dsn, connect_timeout=timeout, **kwargs)
    try:
        original_wait = connection.wait

        def wait(generator: Generator[Any, Any, Any], interval: float = 0.1) -> Any:
            """Keep psycopg's normal readiness handling with a finite socket deadline."""
            bounded = deadline_generator(
                generator,
                deadline=time.monotonic() + SOCKET_SECONDS,
                close=connection.close,
            )
            # psycopg's normal wait loop feeds idle readiness back into the generator.
            return original_wait(bounded, interval=min(interval, 0.1) if interval > 0 else 0.1)

        connection.wait = wait
        return connection
    except Exception:
        connection.close()
        raise
