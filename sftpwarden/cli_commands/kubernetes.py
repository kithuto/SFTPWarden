"""Kubernetes-focused CLI commands for SFTPWarden manifest deployments."""

from __future__ import annotations

from typing import Annotated, TypedDict

import typer
from rich.prompt import Confirm

from sftpwarden.cli_commands.app import kube_app
from sftpwarden.cli_commands.deploy_schema import (
    apply_provider_schema_before_deploy,
    provider_schema_deploy_text,
)
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.config import SFTPWardenConfig, load_config
from sftpwarden.contexts import ContextEntry, resolve_context
from sftpwarden.render.kubernetes import kubernetes_manifest_text, write_kubernetes_manifests
from sftpwarden.services.deploy import (
    kubectl_command,
    kubernetes_deployment_plan,
    translate_command_failure,
)
from sftpwarden.system.commands import run
from sftpwarden.utils.console import console, print_info, print_success, terminal_status
from sftpwarden.utils.errors import SFTPWardenError


class KubernetesCheck(TypedDict):
    """Structured result for one kubectl status check."""

    name: str
    command: list[str]
    returncode: int
    output: str
    message: str
    suggestion: str


@kube_app.command("render")
def kube_render(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Render Kubernetes manifests without applying them."""
    try:
        entry, loaded = _load_context_config(context, config)
        console.print(kubernetes_manifest_text(loaded, entry.root))
    except SFTPWardenError as exc:
        handle_error(exc)


@kube_app.command("apply")
def kube_apply(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Apply rendered Kubernetes manifests with kubectl."""
    try:
        entry = resolve_context(context_name=context, reconcile_config=True)
        loaded = load_config(entry.config) if entry.config else None
        if loaded is None:
            raise SFTPWardenError(
                f"Context {entry.name} has no local sftpwarden.yaml.",
                suggestion="Use a local or remote local-sync context.",
            )
        schema_result = apply_provider_schema_before_deploy(entry, loaded, dry_run=dry_run, yes=yes)
        plan = kubernetes_deployment_plan(entry, loaded)
        if dry_run:
            if json_output:
                print_json(
                    {
                        "dry_run": True,
                        "plan": plan.as_dict(),
                        "provider_schema": (
                            schema_result.as_dict() if schema_result is not None else None
                        ),
                    }
                )
                return
            schema_text = provider_schema_deploy_text(schema_result)
            if schema_text:
                console.print(schema_text)
            console.print(plan.text())
            return
        if not entry.root:
            raise SFTPWardenError(
                f"Context {entry.name} has no local root.",
                suggestion="Use a local or remote local-sync context.",
            )
        if schema_result and schema_result.changed:
            print_success(provider_schema_deploy_text(schema_result))
            if schema_result.backup_path:
                console.print(f"Backup: {schema_result.backup_path}")
        write_kubernetes_manifests(loaded, entry.root)
        commands = [action.command for action in plan.actions if action.command]
        if not commands:
            raise SFTPWardenError("Kubernetes apply command was not generated.")
        results = []
        with terminal_status("Applying Kubernetes manifests"):
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
        print_success("Applied Kubernetes manifests.")
    except SFTPWardenError as exc:
        handle_error(exc)


@kube_app.command("status")
def kube_status(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show Kubernetes namespace, workload, pod, service, PVC and health status."""
    try:
        loaded = _load_config(context, None)
        commands = _status_commands(loaded)
        results = [_command_status(command, name=name) for name, command in commands]
        if json_output:
            print_json(
                {
                    "namespace": loaded.kubernetes.namespace,
                    "release": loaded.kubernetes.release,
                    "checks": results,
                }
            )
            return
        console.print("[bold]Kubernetes status[/bold]")
        console.print(f"Namespace: [bold]{loaded.kubernetes.namespace}[/bold]")
        console.print(f"Release: [bold]{loaded.kubernetes.release}[/bold]")
        for item in results:
            status = "pass" if item["returncode"] == 0 else "fail"
            console.print(f"[bold]{item['name']}[/bold]: {status}")
            _print_check_details(item)
    except SFTPWardenError as exc:
        handle_error(exc)


@kube_app.command("logs")
def kube_logs(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
) -> None:
    """Show runtime pod logs."""
    try:
        loaded = _load_config(context, None)
        target = f"statefulset/{loaded.kubernetes.release}"
        command = kubectl_command(
            loaded,
            ["logs", target, "-c", "sftpwarden", *(["--follow"] if follow else [])],
            namespace=loaded.kubernetes.namespace,
        )
        result = run(command, capture_output=not follow)
        if result.returncode != 0:
            raise translate_command_failure(result)
        console.file.write(result.stdout)
    except SFTPWardenError as exc:
        handle_error(exc)


@kube_app.command("doctor")
def kube_doctor(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate kubectl access, namespace, storage, secrets, probes and provider configuration."""
    try:
        loaded = _load_config(context, None)
        checks = [
            _command_status(
                kubectl_command(loaded, ["version", "--client"]), name="kubectl client"
            ),
            _command_status(
                kubectl_command(loaded, ["get", "namespace", loaded.kubernetes.namespace]),
                name="namespace",
            ),
            _command_status(
                kubectl_command(
                    loaded,
                    ["get", "secret", f"{loaded.kubernetes.release}-host-keys"],
                    namespace=loaded.kubernetes.namespace,
                ),
                name="host-keys secret",
            ),
        ]
        if loaded.kubernetes.storage_class:
            checks.append(
                _command_status(
                    kubectl_command(
                        loaded,
                        ["get", "storageclass", loaded.kubernetes.storage_class],
                    ),
                    name="storage class",
                )
            )
        if json_output:
            print_json({"checks": checks})
            return
        console.print("[bold]Kubernetes doctor[/bold]")
        for check in checks:
            status = "pass" if check["returncode"] == 0 else "warn"
            console.print(f"[bold]{check['name']}[/bold]: {status}")
            _print_check_details(check)
        print_info("Provider and probe configuration are validated during manifest rendering.")
    except SFTPWardenError as exc:
        handle_error(exc)


@kube_app.command("delete")
def kube_delete(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Delete Kubernetes resources with explicit confirmation."""
    try:
        entry = resolve_context(context_name=context, reconcile_config=True)
        loaded = load_config(entry.config) if entry.config else None
        if loaded is None or not entry.root:
            raise SFTPWardenError(
                f"Context {entry.name} has no local Kubernetes project.",
                suggestion="Use a local or remote local-sync context.",
            )
        write_kubernetes_manifests(loaded, entry.root)
        command = kubectl_command(
            loaded,
            ["delete", "-f", "kubernetes.yml", "--ignore-not-found"],
            namespace=loaded.kubernetes.namespace,
        )
        if dry_run:
            console.print(" ".join(command))
            return
        if not yes and not Confirm.ask("Delete Kubernetes resources?", default=False):
            raise typer.Exit(1)
        result = run(command, cwd=entry.root)
        if result.returncode != 0:
            raise translate_command_failure(result)
        print_success("Deleted Kubernetes resources.")
    except SFTPWardenError as exc:
        handle_error(exc)


def _load_config(context: str | None, config: str | None) -> SFTPWardenConfig:
    """Load project configuration for a resolved context."""
    return _load_context_config(context, config)[1]


def _load_context_config(
    context: str | None, config: str | None
) -> tuple[ContextEntry, SFTPWardenConfig]:
    """Resolve a context and load its required local configuration."""
    entry = resolve_context(config_path=config, context_name=context, reconcile_config=True)
    if not entry.config:
        raise SFTPWardenError(
            f"Context {entry.name} has no local sftpwarden.yaml.",
            suggestion="Use a local or remote local-sync context, or pass --config.",
        )
    return entry, load_config(entry.config)


def _status_commands(config: SFTPWardenConfig) -> list[tuple[str, list[str]]]:
    """Build the kubectl commands used by Kubernetes status checks."""
    selector = f"app.kubernetes.io/instance={config.kubernetes.release}"
    namespace = config.kubernetes.namespace
    return [
        ("namespace", kubectl_command(config, ["get", "namespace", namespace])),
        (
            "statefulset",
            kubectl_command(
                config, ["get", "statefulset", config.kubernetes.release], namespace=namespace
            ),
        ),
        ("pods", kubectl_command(config, ["get", "pods", "-l", selector], namespace=namespace)),
        (
            "service",
            kubectl_command(
                config, ["get", "service", config.kubernetes.release], namespace=namespace
            ),
        ),
        ("pvcs", kubectl_command(config, ["get", "pvc", "-l", selector], namespace=namespace)),
    ]


def _command_status(command: list[str], *, name: str | None = None) -> KubernetesCheck:
    """Execute one status command and normalize its diagnostic result."""
    result = run(command)
    message = ""
    suggestion = ""
    if result.returncode != 0:
        error = translate_command_failure(result)
        message = error.message
        suggestion = error.suggestion or ""
    return {
        "name": name or " ".join(command),
        "command": command,
        "returncode": result.returncode,
        "output": result.output,
        "message": message,
        "suggestion": suggestion,
    }


def _print_check_details(check: KubernetesCheck) -> None:
    """Print diagnostic details for one Kubernetes check."""
    message = check.get("message") or check.get("output")
    suggestion = check.get("suggestion")
    if message:
        console.print(f"  {message}")
    if suggestion:
        console.print(f"  [bold yellow]Fix:[/bold yellow] {suggestion}")
