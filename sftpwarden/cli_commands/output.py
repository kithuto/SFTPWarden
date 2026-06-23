from __future__ import annotations

import json
from typing import Any

from rich import box
from rich.table import Table

from sftpwarden.runtime import RuntimePlan
from sftpwarden.utils.console import console, print_info, print_success


def runtime_plan_to_json(runtime_plan: RuntimePlan) -> str:
    """Serialize a runtime plan for CLI JSON output.

    Parameters
    ----------
    runtime_plan
        Runtime plan to serialize.

    Returns
    -------
    str
        Stable, indented JSON document.
    """
    return json.dumps(
        {
            "fingerprint": runtime_plan.fingerprint,
            "changed": runtime_plan.changed,
            "actions": [
                {
                    "action": action.action,
                    "username": action.username,
                    "uid": action.uid,
                    "gid": action.gid,
                    "reason": action.reason,
                }
                for action in runtime_plan.actions
            ],
        },
        indent=2,
        sort_keys=True,
    )


def print_json(data: Any) -> None:
    """Write JSON-compatible data to the configured console file.

    Parameters
    ----------
    data
        JSON string or JSON-serializable object.
    """
    text = data if isinstance(data, str) else json.dumps(data, indent=2, sort_keys=True)
    console.file.write(text)
    console.file.write("\n")
    console.file.flush()


def runtime_plan_explanation(runtime_plan: RuntimePlan, *, apply_command: str) -> str:
    """Explain what a runtime plan means for the next apply command.

    Parameters
    ----------
    runtime_plan
        Runtime plan to explain.
    apply_command
        User-facing command that applies the planned actions.

    Returns
    -------
    str
        Human-readable explanation.
    """
    if runtime_plan.changed:
        return (
            f"User/provider changes detected. These actions will be applied by `{apply_command}`."
        )
    return f"No user/provider changes detected. `{apply_command}` has nothing to apply."


def print_runtime_plan(runtime_plan: RuntimePlan) -> None:
    """Render runtime actions as a Rich table.

    Parameters
    ----------
    runtime_plan
        Runtime plan whose actions should be displayed.
    """
    if not runtime_plan.actions:
        return
    table = Table(title="Runtime sync actions", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Action", style="bold")
    table.add_column("Username", style="cyan")
    table.add_column("UID", justify="right")
    table.add_column("GID", justify="right")
    table.add_column("Reason")
    for action in runtime_plan.actions:
        table.add_row(
            action.action,
            action.username,
            str(action.uid or ""),
            str(action.gid or ""),
            action.reason,
        )
    console.print(table)


def print_deploy_config_plan(reasons: list[str]) -> None:
    """Print deploy-level configuration plan details.

    Parameters
    ----------
    reasons
        Detected configuration changes.
    """
    if not reasons:
        print_success("No deploy-level configuration changes detected.")
        return
    print_info(
        "Configuration/deploy changes detected. These changes will be applied by "
        "`sftpwarden deploy`; `sftpwarden refresh` only applies user/provider changes."
    )
    for reason in reasons:
        console.print(f"  [cyan]-[/cyan] {reason}")


def print_watcher_without_local_sync_targets() -> None:
    """Print guidance for an installed watcher with no local-sync targets."""
    print_info("Watcher is installed but there are no remote local-sync contexts left.")
    console.print("Run `sftpwarden watcher uninstall` if you no longer need it.")
