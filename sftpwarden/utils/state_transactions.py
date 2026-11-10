"""Reentrant, process-wide transactions for host configuration workflows."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import ParamSpec, TypeVar

from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.persistence import file_lock

_held = threading.local()
P = ParamSpec("P")
R = TypeVar("R")


@contextmanager
def state_transaction(path: Path, *, owner: tuple[int, int] | None = None) -> Iterator[None]:
    """Hold a stable file lock across a complete host-state operation.

    Nested workflow helpers share their calling thread's lock. Other threads and
    processes still acquire the OS lock. All callers must acquire the context
    registry before global config when an operation needs both.
    """
    key = os.path.normcase(os.path.abspath(path))
    previous: frozenset[str] = getattr(_held, "paths", frozenset())
    if key in previous:
        yield
        return
    try:
        with file_lock(path, owner=owner):
            _held.paths = previous | {key}
            try:
                yield
            finally:
                _held.paths = previous
    except TimeoutError:
        raise SFTPWardenError(
            f"Timed out waiting for local state: {path}",
            suggestion="Wait for the other SFTPWarden operation to finish and retry.",
        ) from None


def serialized_state(path: Callable[[], Path]) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Serialize an existing mutating workflow before it reads local state."""

    def decorate(operation: Callable[P, R]) -> Callable[P, R]:
        @wraps(operation)
        def serialized(*args: P.args, **kwargs: P.kwargs) -> R:
            with state_transaction(path()):
                return operation(*args, **kwargs)

        return serialized

    return decorate
