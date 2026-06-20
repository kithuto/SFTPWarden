from __future__ import annotations

from typing import Any

import yaml

from sftpwarden.utils.errors import ConfigError


def parse_cli_value(value: str) -> Any:
    """Parse a CLI value using YAML scalar rules.

    Parameters
    ----------
    value
        Raw CLI value.

    Returns
    -------
    Any
        Parsed value.
    """
    return yaml.safe_load(value)


def get_dotted(data: dict[str, Any], path: str) -> Any:
    """Read a nested value from a mapping using dot notation.

    Parameters
    ----------
    data
        Mapping to inspect.
    path
        Dot-separated path.

    Returns
    -------
    Any
        Value stored at the path.
    """
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"Unknown configuration path: {path}")
        current = current[part]
    return current


def set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a nested mapping value using dot notation.

    Parameters
    ----------
    data
        Mapping to mutate.
    path
        Dot-separated path.
    value
        Value to assign.
    """
    parts = path.split(".")
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"Unknown configuration path: {path}")
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise ConfigError(f"Unknown configuration path: {path}")
    current[parts[-1]] = value


def format_value(value: Any) -> str:
    """Format a value for CLI output.

    Parameters
    ----------
    value
        Value to format.

    Returns
    -------
    str
        Human-readable YAML scalar or document.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)
    return yaml.safe_dump(value, sort_keys=False).strip()
