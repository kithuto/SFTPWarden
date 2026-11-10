"""Update management components without deploying SFTP servers."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from rich.prompt import Confirm

from sftpwarden.cli_commands.app import app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.services.upgrade import (
    DEPLOY_REMINDER,
    apply_management_update,
    upgrade_plan,
    upgrade_status,
)
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError


def print_result(result: dict[str, Any]) -> None:
    """Explain component outcomes without claiming a deferred server update."""
    if result.get("status") == "running":
        console.print(
            result.get("message", "Update is running. Check again with `upgrade --status`.")
        )
        return
    if "cli" not in result:
        console.print(f"Update status: {result.get('status', 'unknown')}.")
        if result.get("stage"):
            console.print(f"Last step: {result['stage']}.")
        if result.get("action"):
            console.print(result["action"])
        if result.get("result"):
            print_result(result["result"])
        return
    console.print(f"CLI verified: {result['cli']['applied']}.")
    watcher = result["watcher"]
    if watcher["status"] == "not_installed":
        console.print("Watcher: not installed.")
    elif watcher["status"] == "custom_image":
        console.print(watcher["action"])
    elif watcher.get("activated") is False:
        console.print("Watcher configuration updated; it remains inactive.")
    else:
        console.print(f"Watcher: {watcher['status']}.")
    deployments = result["deployments"]
    if deployments["status"] == "deferred":
        console.print(DEPLOY_REMINDER)
    else:
        for item in deployments["results"]:
            console.print(f"Deployed {item['context']}: {item['applied']} ({item['status']}).")


@app.command()
def upgrade(
    status: Annotated[
        bool,
        typer.Option(
            "--status", help="Read the last update result without contacting the network."
        ),
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Only check release availability.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    target: Annotated[
        str | None, typer.Option("--to", help="Select a published stable release.")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    deploy_all: Annotated[
        bool,
        typer.Option(
            "--deploy-all",
            help="Explicitly authorize deployment of every context when incompatible.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Update management tools; incompatible servers require explicit all-context consent."""
    try:
        if status:
            result = upgrade_status()
            if json_output:
                print_json(result)
            else:
                print_result(result)
            if result["status"] in {"incomplete", "pending"}:
                raise typer.Exit(1)
            return
        plan = upgrade_plan(target, check_only=check)
        if check or dry_run:
            if json_output:
                print_json(plan)
            else:
                console.print(
                    f"Installed CLI: {plan['cli']['previous']}; "
                    f"published release: {plan['available']}."
                )
                if dry_run:
                    console.print(json.dumps(plan, indent=2))
                console.print(DEPLOY_REMINDER)
            return
        coordinated = plan.get("deployments", {}).get("requires_deploy_all", False)
        if coordinated and yes and not deploy_all:
            message = (
                "Incompatible deployments require explicit --yes --deploy-all; nothing was changed."
            )
            if json_output:
                print_json({"status": "deploy_all_confirmation_required", "plan": plan})
                raise typer.Exit(1)
            raise SFTPWardenError(message)
        if json_output and not yes:
            print_json({"status": "confirmation_required", "plan": plan})
            raise typer.Exit(1)
        if not json_output:
            console.print(
                f"CLI {plan['cli']['previous']} -> {plan['cli']['target']}; "
                f"watcher: {plan['watcher']['status']}."
            )
            console.print("A verified backup is recommended. The watcher may restart.")
            if coordinated:
                for item in plan["deployments"]["incompatible"]:
                    console.print(f"Incompatible: {item['context']}: {item['reason']}")
                names = ", ".join(item["name"] for item in plan["deployments"]["contexts"])
                console.print(
                    f"Continuing deploys ALL contexts: {names}. Active transfers may stop."
                )
            else:
                console.print("SFTP servers will not be deployed.")
        prompt = (
            "Update and deploy ALL listed contexts?"
            if coordinated
            else "Update the CLI and installed watcher?"
        )
        if not yes and not Confirm.ask(prompt, default=False):
            raise typer.Exit(1)
        plan["deploy_all_authorized"] = bool(coordinated)
        result = apply_management_update(plan, json_output=json_output)
        if json_output:
            print_json(result)
        else:
            print_result(result)
    except SFTPWardenError as exc:
        if json_output:
            print_json({"status": "failed", "message": exc.message, "action": exc.suggestion})
            raise typer.Exit(1) from None
        handle_error(exc)
