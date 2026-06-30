"""Helm-focused CLI commands for SFTPWarden Kubernetes deployments."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import typer
from rich.prompt import Confirm

from sftpwarden.cli_commands.app import helm_app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.config import SFTPWardenConfig, load_config
from sftpwarden.contexts import resolve_context
from sftpwarden.render.kubernetes import HELM_VALUES_FILE, helm_values_text, write_helm_values
from sftpwarden.services.deploy import (
    HelmChartReference,
    helm_chart_reference,
    helm_command,
    helm_deployment_plan,
    translate_command_failure,
)
from sftpwarden.system.commands import CommandResult, run
from sftpwarden.utils.console import console, print_success, terminal_status
from sftpwarden.utils.errors import SFTPWardenError


@helm_app.command("values")
def helm_values(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    write: Annotated[bool, typer.Option("--write")] = False,
) -> None:
    """Generate starter Helm values."""
    try:
        entry, loaded = _load_context_config(context, config)
        if write:
            if not entry.root:
                raise SFTPWardenError(
                    f"Context {entry.name} has no local root.",
                    suggestion="Use a local or remote local-sync context.",
                )
            target = write_helm_values(loaded, entry.root)
            print_success(f"Wrote {target}")
            return
        console.print(helm_values_text(loaded, entry.root))
    except SFTPWardenError as exc:
        handle_error(exc)


@helm_app.command("template")
def helm_template(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Render the official chart locally with Helm."""
    try:
        entry, loaded = _load_context_config(context, None)
        if not entry.root:
            raise SFTPWardenError(
                f"Context {entry.name} has no local root.",
                suggestion="Use a local or remote local-sync context.",
            )
        write_helm_values(loaded, entry.root)
        chart = helm_chart_reference()
        command = helm_command(
            loaded,
            [
                "template",
                loaded.kubernetes.release,
                *chart.command_args(),
                "--namespace",
                loaded.kubernetes.namespace,
                "--values",
                HELM_VALUES_FILE,
            ],
        )
        result = run(command, cwd=entry.root)
        if result.returncode != 0:
            raise translate_command_failure(result)
        if json_output:
            print_json({"command": command, "output": result.stdout})
            return
        console.file.write(result.stdout)
    except SFTPWardenError as exc:
        handle_error(exc)


@helm_app.command("lint")
def helm_lint(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
) -> None:
    """Validate the official chart with Helm."""
    try:
        entry, loaded = _load_context_config(context, None)
        if entry.root:
            write_helm_values(loaded, entry.root)
        chart = helm_chart_reference()
        result = _run_helm_lint(loaded, chart, cwd=entry.root)
        if result.returncode != 0:
            raise translate_command_failure(result)
        console.file.write(result.stdout)
        print_success("Helm chart lint passed.")
    except SFTPWardenError as exc:
        handle_error(exc)


@helm_app.command("upgrade")
def helm_upgrade(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    install: Annotated[bool, typer.Option("--install")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Install or upgrade the Helm release."""
    try:
        entry, loaded = _load_context_config(context, None)
        plan = helm_deployment_plan(entry, loaded)
        commands = _helm_upgrade_commands(plan, install=install)
        if not commands:
            raise SFTPWardenError("Helm upgrade command was not generated.")
        if dry_run:
            if json_output:
                print_json(
                    {
                        "dry_run": True,
                        "plan": plan.as_dict(),
                        "command": commands[0],
                        "commands": commands,
                    }
                )
                return
            console.print("\n".join(" ".join(command) for command in commands))
            return
        if entry.root:
            write_helm_values(loaded, entry.root)
        results = []
        with terminal_status("Applying Helm release"):
            for command in commands:
                result = run(command, cwd=entry.root)
                if result.returncode != 0:
                    raise translate_command_failure(result)
                results.append(result.stdout.strip())
        if json_output:
            print_json(
                {
                    "dry_run": False,
                    "plan": plan.as_dict(),
                    "result": "\n".join(output for output in results if output),
                }
            )
            return
        print_success("Helm release applied.")
    except SFTPWardenError as exc:
        handle_error(exc)


@helm_app.command("uninstall")
def helm_uninstall(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Uninstall the Helm release with explicit confirmation."""
    try:
        _entry, loaded = _load_context_config(context, None)
        command = helm_command(
            loaded,
            ["uninstall", loaded.kubernetes.release, "--namespace", loaded.kubernetes.namespace],
        )
        if dry_run:
            console.print(" ".join(command))
            return
        if not yes and not Confirm.ask("Uninstall Helm release?", default=False):
            raise typer.Exit(1)
        result = run(command)
        if result.returncode != 0:
            raise translate_command_failure(result)
        print_success("Helm release uninstalled.")
    except SFTPWardenError as exc:
        handle_error(exc)


def _load_context_config(context: str | None, config: str | None):
    entry = resolve_context(config_path=config, context_name=context, reconcile_config=True)
    if not entry.config:
        raise SFTPWardenError(
            f"Context {entry.name} has no local sftpwarden.yaml.",
            suggestion="Use a local or remote local-sync context, or pass --config.",
        )
    return entry, load_config(entry.config)


def _helm_upgrade_commands(plan, *, install: bool) -> list[list[str]]:
    commands = [list(action.command) for action in plan.actions if action.command]
    if install:
        return commands
    filtered = []
    for command in commands:
        if command and command[0] == "helm" and "upgrade" in command:
            filtered.append([part for part in command if part != "--install"])
            continue
        filtered.append(command)
    return filtered


def _run_helm_lint(
    config: SFTPWardenConfig, chart: HelmChartReference, *, cwd: str | None
) -> CommandResult:
    """Run Helm lint, pulling the published OCI chart when no local chart exists."""
    if chart.local:
        return run(helm_command(config, ["lint", chart.reference]), cwd=cwd)
    if chart.version is None:
        raise SFTPWardenError(
            "Cannot lint the published Helm chart without a chart version.",
            suggestion="Install a released SFTPWarden package or run from a source checkout.",
        )
    with TemporaryDirectory(prefix="sftpwarden-chart-") as temp_dir:
        pull_command = helm_command(
            config,
            [
                "pull",
                chart.reference,
                "--version",
                chart.version,
                "--untar",
                "--untardir",
                temp_dir,
            ],
        )
        pull_result = run(pull_command, cwd=cwd)
        if pull_result.returncode != 0:
            return pull_result
        chart_path = Path(temp_dir) / "sftpwarden"
        return run(helm_command(config, ["lint", str(chart_path)]), cwd=cwd)
