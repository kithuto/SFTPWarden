from __future__ import annotations

import re
from pathlib import Path


def validate_relative_safe_path(value: str, *, field_name: str) -> None:
    """Validate a relative path that cannot traverse directories.

    Parameters
    ----------
    value
        Path value to validate.
    field_name
        Field name used in error messages.
    """
    normalized = value.replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or normalized.startswith("/"):
        raise ValueError(f"{field_name} must be relative.")
    if not value or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(f"{field_name} must not contain empty, current, or parent segments.")


def validate_provider_path(value: str | Path) -> None:
    """Validate a provider path for unsafe segments.

    Parameters
    ----------
    value
        Provider path value.
    """
    path = Path(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("provider.path must not contain empty, current, or parent segments.")


def validate_octal_permissions(value: str, *, field_name: str) -> None:
    """Validate an octal permission string.

    Parameters
    ----------
    value
        Permission string.
    field_name
        Field name used in error messages.
    """
    if not re.fullmatch(r"0?[0-7]{3}", value):
        raise ValueError(f"{field_name} must be a three-digit octal permission string.")
