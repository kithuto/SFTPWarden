"""Partition release checks without omitting new or architecture-specific cases."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pytest

EXTERNAL_GROUPS = {
    "policy": {
        "test_auth_policy.py",
        "test_malformed_public_key.py",
        "test_invalid_file_policy.py",
        "test_identity_safety.py",
        "test_incremental_policy.py",
        "test_applied_config.py",
        "test_credential_lifecycle.py",
        "test_custom_compose_refresh.py",
        "test_invalid_sql_policy.py",
    },
    "deployment": {
        "test_docker_compose_runtime.py",
        "test_schema_deployment.py",
        "test_remote_preparation.py",
        "test_remote_compose_artifacts.py",
        "test_upgrade_recovery.py",
        "test_context_cleanup.py",
    },
    "delivery": {"test_watcher_ssh.py", "test_provider_transport.py", "test_database_providers.py"},
    "databases": {
        "test_provider_concurrency.py",
        "test_sql_read_snapshot.py",
        "test_readonly_runtime.py",
    },
    "recovery": {
        "test_external_runtime_recovery.py",
        "test_fresh_recovery.py",
        "test_database_restore.py",
        "test_schema_database_recovery.py",
    },
}
GROUPS = ("contracts", "load", "cluster", *EXTERNAL_GROUPS)


def group_for(path: Path, markers: set[str]) -> str:
    """Assign each collected case exactly once; unknown external cases block CI."""
    if markers & {"release_kubernetes", "release_helm"}:
        return "cluster"
    if "release_external" not in markers:
        return "contracts"
    if path.name == "test_bounded_load.py":
        return "load"
    matches = [name for name, files in EXTERNAL_GROUPS.items() if path.name in files]
    if len(matches) != 1:
        raise ValueError(f"Assign external release module {path.name} to exactly one group.")
    return matches[0]


class Selection:
    """Validate the complete inventory before selecting an independent execution group."""

    def __init__(self, group: str) -> None:
        self.group = group

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        """Validate every case before retaining this group's independent selection."""
        selected, deselected = [], []
        counts: Counter[str] = Counter()
        for item in items:
            try:
                group = group_for(Path(item.path), {mark.name for mark in item.iter_markers()})
            except ValueError as exc:
                raise pytest.UsageError(str(exc)) from None
            counts[group] += 1
            (selected if group == self.group else deselected).append(item)
        if not selected:
            raise pytest.UsageError(f"No release cases selected for {self.group}.")
        reporter = config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"Complete release inventory: {dict(sorted(counts.items()))}")
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)


def main() -> int:
    """Run one complete group against the installed candidate, with normal pytest exit codes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=GROUPS)
    args, pytest_args = parser.parse_known_args()
    return pytest.main(
        ["tests/release_validation", "--run-release-validation", "--durations=20", *pytest_args],
        plugins=[Selection(args.group)],
    )


if __name__ == "__main__":
    sys.exit(main())
