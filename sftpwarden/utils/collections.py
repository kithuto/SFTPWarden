from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def unique_items(values: Iterable[T]) -> list[T]:
    """Return unique values while preserving input order.

    Parameters
    ----------
    values
        Input values.

    Returns
    -------
    list[T]
        Unique values in first-seen order.
    """
    return list(dict.fromkeys(values))
