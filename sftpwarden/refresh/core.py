from __future__ import annotations

import shlex
from pathlib import Path

from sftpwarden.config import DeployTarget, SFTPWardenConfig, load_config
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    ContextType,
    load_registry,
    require_initialized_context,
    resolve_context,
)
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.render.kubernetes import kubernetes_resource_name
from sftpwarden.services.context_cleanup import ensure_remote_only_root_available
from sftpwarden.services.deploy import kubectl_command
from sftpwarden.system.commands import command_text, run_checked
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.errors import ContextError, RuntimeError


def docker_compose_command(context: ContextEntry) -> list[str]:
    """Build the Docker Compose runtime refresh command.

    Parameters
    ----------
    context
        Context whose runtime should be refreshed.

    Returns
    -------
    list[str]
        Docker Compose command arguments.
    """
    compose_file = "docker-compose.yml"
    if context.type == ContextType.REMOTE and context.remote:
        compose_file = context.remote.compose_file
    return [
        "docker",
        "compose",
        "-f",
        compose_file,
        "exec",
        "-T",
        "sftpwarden",
        "sftpwarden",
        "runtime",
        "refresh",
    ]


def kubernetes_refresh_command(config: SFTPWardenConfig) -> list[str]:
    """Build the kubectl command that refreshes the runtime pod.

    Parameters
    ----------
    config
        Project config for the Kubernetes deployment.

    Returns
    -------
    list[str]
        kubectl exec command arguments.
    """
    pod_name = f"{kubernetes_resource_name(config.kubernetes.release)}-0"
    return kubectl_command(
        config,
        [
            "exec",
            pod_name,
            "-c",
            "sftpwarden",
            "--",
            "sftpwarden",
            "runtime",
            "refresh",
            "--config",
            CONTAINER_CONFIG_PATH,
        ],
        namespace=config.kubernetes.namespace,
    )


def refresh_context(context: ContextEntry, *, dry_run: bool = False) -> str:
    """Refresh a local or remote runtime context.

    Parameters
    ----------
    context
        Context to refresh.
    dry_run
        Whether to return the command without executing it.

    Returns
    -------
    str
        Refresh output or dry-run command text.

    Raises
    ------
    ContextError
        Raised when remote context settings are incomplete.
    RuntimeError
        Raised when the refresh command fails.
    """
    if context.type == ContextType.LOCAL:
        kubernetes_config = _kubernetes_config_if_available(context)
        if kubernetes_config:
            command = kubernetes_refresh_command(kubernetes_config)
            if dry_run:
                return "(dry-run) " + command_text(command)
            result = run_checked(
                command,
                error_type=RuntimeError,
                message=f"Kubernetes refresh failed for context {context.name}.",
                fallback_suggestion="Verify kubectl access and that the runtime pod is ready.",
            )
            return result.stdout.strip() or f"Refreshed {context.name}."
        command = docker_compose_command(context)
        cwd = context.root or "."
        if dry_run:
            return f"(dry-run) cd {cwd} && {' '.join(command)}"
        result = run_checked(
            command,
            cwd=cwd,
            error_type=RuntimeError,
            message=f"Refresh failed for context {context.name}.",
            fallback_suggestion="Start the runtime with `sftpwarden deploy`.",
        )
        return result.stdout.strip() or f"Refreshed {context.name}."

    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    if not dry_run:
        ensure_remote_only_root_available(context)

    command = " ".join(shlex.quote(part) for part in docker_compose_command(context))
    remote_command = f"cd {shlex.quote(context.remote.remote_root)} && {command}"
    ssh = ssh_base_command(context.remote)
    ssh.append(remote_command)
    if dry_run:
        return "(dry-run) " + command_text(ssh)
    result = run_checked(
        ssh,
        error_type=RuntimeError,
        message=f"Remote refresh failed for context {context.name}.",
        fallback_suggestion="Verify SSH access and that the remote runtime is running.",
    )
    return result.stdout.strip() or f"Refreshed {context.name}."


def _kubernetes_config_if_available(context: ContextEntry) -> SFTPWardenConfig | None:
    if not context.config:
        return None
    config_path = Path(context.config)
    if not config_path.exists():
        return None
    config = load_config(config_path)
    if config.deploy.target != DeployTarget.KUBERNETES:
        return None
    return config


def resolve_refresh_targets(
    *,
    all_contexts: bool = False,
    context_name: str | None = None,
    config_path: str | None = None,
) -> list[ContextEntry]:
    """Resolve contexts targeted by a refresh command.

    Parameters
    ----------
    all_contexts
        Whether to include every registered context.
    context_name
        Optional context name to resolve.
    config_path
        Optional explicit config path.

    Returns
    -------
    list[ContextEntry]
        Contexts to refresh.

    Raises
    ------
    ContextError
        Raised when no context can be resolved.
    """
    if all_contexts:
        require_initialized_context()
        registry: ContextRegistry = load_registry()
        targets = list(registry.contexts.values())
        if not targets:
            raise ContextError(
                "No contexts are registered.",
                suggestion="Run `sftpwarden init <name>` first.",
            )
        return targets
    return [resolve_context(config_path=config_path, context_name=context_name)]
