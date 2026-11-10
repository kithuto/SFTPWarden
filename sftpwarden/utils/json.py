"""JSON decoding shared by provider documents and nested metadata fields."""

from __future__ import annotations

import json
import math
from typing import Any


def _unique_mapping(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise ValueError("Duplicate mapping key")
        mapping[key] = value
    return mapping


def _reject_constant(_value: str) -> Any:
    raise ValueError("Non-finite JSON number")


def strict_json_value(text: str) -> Any:
    """Parse JSON without duplicate object fields or non-finite numeric extensions."""
    return json.loads(text, object_pairs_hook=_unique_mapping, parse_constant=_reject_constant)


def validate_finite_json_numbers(value: Any) -> None:
    """Reject non-finite numbers before serialization can silently turn them into null."""
    pending = [value]
    visited: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON metadata numbers must be finite.")
        if isinstance(item, (dict, list, tuple, set, frozenset)) and id(item) not in visited:
            visited.add(id(item))
            pending.extend(item.values() if isinstance(item, dict) else item)
