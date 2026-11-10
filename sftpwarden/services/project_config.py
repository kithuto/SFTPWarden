"""Checked field mutations of shared project configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sftpwarden.config import SFTPWardenConfig, load_config, write_config
from sftpwarden.utils.dotted import get_dotted, set_dotted
from sftpwarden.utils.errors import ConfigError
from sftpwarden.utils.paths import expand_path
from sftpwarden.utils.state_transactions import state_transaction


def update_project_field(
    config_path: str | Path,
    field: str,
    value: Any,
    *,
    expected: SFTPWardenConfig,
) -> SFTPWardenConfig:
    """Merge a selected field into fresh config, rejecting a conflicting edit."""
    path = expand_path(config_path)
    with state_transaction(path):
        current = load_config(path).model_dump(mode="json")
        previous = expected.model_dump(mode="json")
        # Schema confirmation/migration belongs to the provider that was inspected.
        comparison = "provider" if field == "provider.user_schema" else field
        if get_dotted(current, comparison) != get_dotted(previous, comparison):
            raise ConfigError(
                "Project configuration changed concurrently; the selected field was not saved.",
                suggestion="Reload the project configuration and retry the change.",
            )
        set_dotted(current, field, value)
        updated = SFTPWardenConfig.model_validate(current)
        write_config(path, updated)
        return updated
