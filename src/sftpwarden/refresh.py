from __future__ import annotations

import shlex
import subprocess

from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    ContextType,
    load_registry,
    resolve_context,
)
from sftpwarden.remote.ssh import uses_default_ssh_identity
from sftpwarden.utils.errors import ContextError, RuntimeError


def docker_compose_command(context: ContextEntry) -> list[str]:
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


def refresh_context(context: ContextEntry, *, dry_run: bool = False) -> str:
    if context.type == ContextType.LOCAL:
        command = docker_compose_command(context)
        cwd = context.root or "."
        if dry_run:
            return f"(dry-run) cd {cwd} && {' '.join(command)}"
        result = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Refresh failed for context {context.name}.",
                suggestion=(
                    result.stderr
                    or "Start the runtime with `sftpwarden deploy` or `docker compose up -d`."
                ).strip(),
            )
        return result.stdout.strip() or f"Refreshed {context.name}."

    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")

    command = " ".join(shlex.quote(part) for part in docker_compose_command(context))
    remote_command = f"cd {shlex.quote(context.remote.remote_root)} && {command}"
    ssh = ["ssh", "-p", str(context.remote.port)]
    if not uses_default_ssh_identity(context.remote.ssh_key):
        ssh.extend(["-i", context.remote.ssh_key])  # type: ignore
    ssh.append(f"{context.remote.user}@{context.remote.host}")
    ssh.append(remote_command)
    if dry_run:
        return "(dry-run) " + " ".join(shlex.quote(part) for part in ssh)
    result = subprocess.run(ssh, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Remote refresh failed for context {context.name}.",
            suggestion=(
                result.stderr or "Verify SSH access and that the remote runtime is running."
            ).strip(),
        )
    return result.stdout.strip() or f"Refreshed {context.name}."


def resolve_refresh_targets(
    *,
    all_contexts: bool = False,
    context_name: str | None = None,
    config_path: str | None = None,
) -> list[ContextEntry]:
    if all_contexts:
        registry: ContextRegistry = load_registry()
        targets = list(registry.contexts.values())
        if not targets:
            raise ContextError(
                "No contexts are registered.",
                suggestion="Run `sftpwarden init <name>` or `sftpwarden context add <name>`.",
            )
        return targets
    return [resolve_context(config_path=config_path, context_name=context_name)]
