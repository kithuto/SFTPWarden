from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sftpwarden.config import RemoteStorage, load_config, provider_local_path
from sftpwarden.contexts import ContextEntry, ContextType, RemoteEndpoint
from sftpwarden.remote.checks import ssh_base_command, verify_remote_runtime_requirements
from sftpwarden.remote.ssh import uses_default_ssh_identity
from sftpwarden.render.compose import write_compose
from sftpwarden.utils.errors import ContextError, RuntimeError

NEVER_SYNC_NAMES = {".env", ".git", "data", "state", "host_keys", "__pycache__"}


@dataclass(frozen=True)
class DeployPlan:
    commands: list[list[str]]

    def text(self) -> str:
        return "\n".join(shlex.join(command) for command in self.commands)


def remote_shell_command(remote: RemoteEndpoint, command: str) -> list[str]:
    return [*ssh_base_command(remote), command]


def remote_compose_command(remote: RemoteEndpoint, command: str) -> str:
    return (
        f"cd {shlex.quote(remote.remote_root)} && "
        f"docker compose -f {shlex.quote(remote.compose_file)} {command}"
    )


def remote_rsync_command(files: list[Path], remote: RemoteEndpoint) -> list[str]:
    transport = ["ssh", "-p", str(remote.port)]
    if not uses_default_ssh_identity(remote.ssh_key):
        transport.extend(["-i", remote.ssh_key or ""])
    return [
        "rsync",
        "-az",
        "--protect-args",
        "-e",
        shlex.join(transport),
        *[str(path) for path in files],
        f"{remote.user}@{remote.host}:{remote.remote_root.rstrip('/')}/",
    ]


def local_compose_command(context: ContextEntry, command: str) -> list[str]:
    compose_file = "docker-compose.yml"
    if context.config:
        config = load_config(context.config)
        compose_file = config.docker.compose_file
    return ["docker", "compose", "-f", compose_file, *shlex.split(command)]


def deploy_plan(context: ContextEntry) -> DeployPlan:
    if context.type == ContextType.LOCAL:
        return local_deploy_plan(context)
    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    if context.storage == RemoteStorage.REMOTE_ONLY:
        return remote_only_deploy_plan(context)
    return remote_local_sync_deploy_plan(context)


def local_deploy_plan(context: ContextEntry) -> DeployPlan:
    if not context.root or not context.config:
        raise ContextError(f"Local context {context.name} is missing local settings.")
    config = load_config(context.config)
    write_compose(config, context.root)
    return DeployPlan(
        commands=[
            ["docker", "compose", "-f", config.docker.compose_file, "pull"],
            ["docker", "compose", "-f", config.docker.compose_file, "up", "-d"],
            ["docker", "compose", "-f", config.docker.compose_file, "ps", "sftpwarden"],
        ]
    )


def remote_local_sync_deploy_plan(context: ContextEntry) -> DeployPlan:
    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    if not context.root or not context.config:
        raise ContextError(f"Remote local-sync context {context.name} is missing local settings.")
    config = load_config(context.config)
    compose_path = write_compose(config, context.root)
    files = required_sync_files(
        Path(context.root),
        config_path=Path(context.config),
        compose_path=compose_path,
    )
    mkdir_command = f"mkdir -p {shlex.quote(context.remote.remote_root)}"
    return DeployPlan(
        commands=[
            remote_shell_command(context.remote, mkdir_command),
            remote_rsync_command(files, context.remote),
            remote_shell_command(context.remote, remote_compose_command(context.remote, "pull")),
            remote_shell_command(context.remote, remote_compose_command(context.remote, "up -d")),
            remote_shell_command(
                context.remote,
                remote_compose_command(context.remote, "ps sftpwarden"),
            ),
        ]
    )


def remote_only_deploy_plan(context: ContextEntry) -> DeployPlan:
    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    remote = context.remote
    validate = (
        f"test -f {shlex.quote(remote.remote_config)} && "
        f"test -f {shlex.quote(remote.remote_root.rstrip('/') + '/' + remote.compose_file)}"
    )
    return DeployPlan(
        commands=[
            remote_shell_command(remote, validate),
            remote_shell_command(remote, remote_compose_command(remote, "pull")),
            remote_shell_command(remote, remote_compose_command(remote, "up -d")),
            remote_shell_command(remote, remote_compose_command(remote, "ps sftpwarden")),
        ]
    )


def required_sync_files(root: Path, *, config_path: Path, compose_path: Path) -> list[Path]:
    config = load_config(config_path)
    provider_path = provider_local_path(root, config)
    files = [config_path, provider_path, compose_path]
    for path in files:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
        has_excluded_part = any(part in NEVER_SYNC_NAMES for part in relative_parts)
        if path.name in NEVER_SYNC_NAMES or has_excluded_part:
            raise RuntimeError(f"Refusing to sync excluded path: {path}")
        if not path.exists():
            raise RuntimeError(f"Required deploy file does not exist: {path}")
    return files


def deploy_context(context: ContextEntry, *, dry_run: bool = False) -> str:
    plan = deploy_plan(context)
    if dry_run:
        return plan.text()
    if context.type == ContextType.REMOTE:
        if not context.remote:
            raise ContextError(f"Remote context {context.name} is missing remote settings.")
        verify_remote_runtime_requirements(context.remote)
    for command in plan.commands:
        run_command(command, cwd=context.root if context.type == ContextType.LOCAL else None)
    return f"Deployed {context.name}."


def run_command(command: list[str], *, cwd: str | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Deploy command failed: {shlex.join(command)}",
            suggestion=(result.stderr or result.stdout or "Inspect command output.").strip(),
        )
