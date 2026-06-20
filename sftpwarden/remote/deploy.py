from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sftpwarden.config import ProviderType, RemoteStorage, load_config, provider_local_path
from sftpwarden.contexts import ContextEntry, ContextType, RemoteEndpoint
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.remote.ssh import rsync_ssh_transport, ssh_base_command
from sftpwarden.render.compose import write_compose
from sftpwarden.system.commands import command_text, run_checked
from sftpwarden.utils.errors import ContextError, RuntimeError

NEVER_SYNC_NAMES = {".env", ".git", "data", "state", "host_keys", "__pycache__"}


class CommandRunner(Protocol):
    """Callable used to execute deployment commands.

    Parameters
    ----------
    command
        Command arguments to execute.
    cwd
        Optional working directory.
    """

    def __call__(self, command: list[str], *, cwd: str | None = None) -> None: ...


@dataclass(frozen=True)
class DeployPlan:
    """Commands required to deploy a context.

    Attributes
    ----------
    commands
        Ordered command argument lists.
    """

    commands: list[list[str]]

    def text(self) -> str:
        """Render the deployment plan for dry-run output.

        Returns
        -------
        str
            Human-readable command list.
        """
        return "\n".join(command_text(command) for command in self.commands)


def remote_shell_command(remote: RemoteEndpoint, command: str) -> list[str]:
    """Build an SSH command that runs a remote shell command.

    Parameters
    ----------
    remote
        Remote endpoint.
    command
        Shell command to run remotely.

    Returns
    -------
    list[str]
        SSH command argument list.
    """
    return [*ssh_base_command(remote), command]


def remote_compose_command(remote: RemoteEndpoint, command: str) -> str:
    """Build a remote Docker Compose shell command.

    Parameters
    ----------
    remote
        Remote endpoint containing root and compose file paths.
    command
        Docker Compose subcommand.

    Returns
    -------
    str
        Shell command safe for remote SSH execution.
    """
    return (
        f"cd {shlex.quote(remote.remote_root)} && "
        f"docker compose -f {shlex.quote(remote.compose_file)} {command}"
    )


def remote_rsync_command(files: list[Path], remote: RemoteEndpoint) -> list[str]:
    """Build an rsync command for syncing project files to a remote host.

    Parameters
    ----------
    files
        Local files to sync.
    remote
        Remote endpoint.

    Returns
    -------
    list[str]
        Rsync command argument list.
    """
    return [
        "rsync",
        "-az",
        "--protect-args",
        "-e",
        rsync_ssh_transport(remote),
        *[str(path) for path in files],
        f"{remote.user}@{remote.host}:{remote.remote_root.rstrip('/')}/",
    ]


def deploy_plan(context: ContextEntry) -> DeployPlan:
    """Create a deployment plan for a context.

    Parameters
    ----------
    context
        Context to deploy.

    Returns
    -------
    DeployPlan
        Commands needed to deploy the context.

    Raises
    ------
    ContextError
        Raised when the context is missing required settings.
    """
    if context.type == ContextType.LOCAL:
        return _local_deploy_plan(context)
    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    if context.storage == RemoteStorage.REMOTE_ONLY:
        return _remote_only_deploy_plan(context)
    return _remote_local_sync_deploy_plan(context)


def _local_deploy_plan(context: ContextEntry) -> DeployPlan:
    if not context.root or not context.config:
        raise ContextError(f"Local context {context.name} is missing local settings.")
    config = load_config(context.config)
    write_compose(config, context.root)
    return DeployPlan(
        commands=[
            ["docker", "compose", "-f", config.docker.compose_file, "pull"],
            ["docker", "compose", "-f", config.docker.compose_file, "up", "-d", "--build"],
            ["docker", "compose", "-f", config.docker.compose_file, "ps", "sftpwarden"],
        ]
    )


def _remote_local_sync_deploy_plan(context: ContextEntry) -> DeployPlan:
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
            remote_shell_command(
                context.remote, remote_compose_command(context.remote, "up -d --build")
            ),
            remote_shell_command(
                context.remote,
                remote_compose_command(context.remote, "ps sftpwarden"),
            ),
        ]
    )


def _remote_only_deploy_plan(context: ContextEntry) -> DeployPlan:
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
            remote_shell_command(remote, remote_compose_command(remote, "up -d --build")),
            remote_shell_command(remote, remote_compose_command(remote, "ps sftpwarden")),
        ]
    )


def required_sync_files(root: Path, *, config_path: Path, compose_path: Path) -> list[Path]:
    """Return local files that must be synced for remote local-sync deploys.

    Parameters
    ----------
    root
        Local project root.
    config_path
        Local project config path.
    compose_path
        Generated Docker Compose path.

    Returns
    -------
    list[Path]
        Required files to sync.

    Raises
    ------
    RuntimeError
        Raised when a required file is missing or excluded from syncing.
    """
    config = load_config(config_path)
    files = [config_path, compose_path]
    if config.provider.type not in {ProviderType.MYSQL, ProviderType.POSTGRESQL}:
        files.insert(1, provider_local_path(root, config))
    for path in files:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
        has_excluded_part = any(part in NEVER_SYNC_NAMES for part in relative_parts)
        if path.name in NEVER_SYNC_NAMES or has_excluded_part:
            raise RuntimeError(f"Refusing to sync excluded path: {path}")
        if not path.exists():
            raise RuntimeError(f"Required deploy file does not exist: {path}")
    return files


def deploy_context(
    context: ContextEntry,
    *,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> str:
    """Deploy a context or return the dry-run plan.

    Parameters
    ----------
    context
        Context to deploy.
    dry_run
        Whether to return commands without executing them.
    runner
        Optional command runner used for tests or custom execution.

    Returns
    -------
    str
        Deployment result or dry-run command text.

    Raises
    ------
    ContextError
        Raised when remote requirements are missing.
    RuntimeError
        Raised when a deployment command fails.
    """
    plan = deploy_plan(context)
    if dry_run:
        return plan.text()
    command_runner = runner or run_command
    if context.type == ContextType.LOCAL:
        ensure_local_docker_compose(context, command_runner)
    if context.type == ContextType.REMOTE:
        if not context.remote:
            raise ContextError(f"Remote context {context.name} is missing remote settings.")
        verify_remote_runtime_requirements(context.remote)
    for command in plan.commands:
        command_runner(command, cwd=context.root if context.type == ContextType.LOCAL else None)
    return f"Deployed {context.name}."


def ensure_local_docker_compose(context: ContextEntry, runner: CommandRunner) -> None:
    """Verify Docker Compose is available before local deployment.

    Parameters
    ----------
    context
        Local context being deployed.
    runner
        Command runner used for the check.
    """
    try:
        runner(["docker", "compose", "version"], cwd=context.root or None)
    except RuntimeError as exc:
        raise RuntimeError(
            "Docker Compose is not available.",
            suggestion="Install Docker Compose v2 and retry `sftpwarden deploy`.",
        ) from exc


def run_command(command: list[str], *, cwd: str | None = None) -> None:
    """Run a deployment command.

    Parameters
    ----------
    command
        Command arguments.
    cwd
        Optional working directory.

    Raises
    ------
    RuntimeError
        Raised when the command fails.
    """
    run_checked(
        command,
        cwd=cwd,
        error_type=RuntimeError,
        message=f"Deploy command failed: {command_text(command)}",
        fallback_suggestion="Inspect command output.",
    )
