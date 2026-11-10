from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from sftpwarden.config import FILE_PROVIDER_TYPES, RemoteStorage, load_config, provider_local_path
from sftpwarden.contexts import ContextEntry, ContextType, RemoteEndpoint
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.remote.preparation import PREPARATION_FILE, validate_relative_config
from sftpwarden.remote.ssh import remote_path_expression as remote_path_expression
from sftpwarden.remote.ssh import rsync_ssh_transport, scp_upload_command, ssh_base_command
from sftpwarden.render.compose import (
    GHCR_RUNTIME_IMAGE_REPOSITORY,
    runtime_image_reference,
    write_compose,
)
from sftpwarden.services.provider_layout import complete_provider_layout, migrate_provider_layout
from sftpwarden.system.commands import CommandResult, command_text, run_checked
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import ContextError, RuntimeError
from sftpwarden.utils.platform import system_is

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

    def __call__(self, command: list[str], *, cwd: str | None = None) -> None:
        """Execute one deployment command."""
        ...


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
        f"cd {remote_path_expression(remote.remote_root)} && "
        f"docker compose -f {shlex.quote(remote.compose_file)} {command}"
    )


def remote_rsync_command(
    files: list[Path], remote: RemoteEndpoint, *, root: Path | None = None
) -> list[str]:
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
        *(["--relative"] if root is not None else []),
        "--protect-args",
        "-e",
        rsync_ssh_transport(remote),
        *[
            f"{root.absolute()}/./{path.absolute().relative_to(root.absolute()).as_posix()}"
            if root is not None
            else str(path)
            for path in files
        ],
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
    """Build the Docker Compose deployment plan for a local context."""
    if not context.root or not context.config:
        raise ContextError(f"Local context {context.name} is missing local settings.")
    config = load_config(context.config)
    image = runtime_image_reference(config, allow_local_build=True)
    commands = []
    if not image.local_build:
        commands.append(["docker", "compose", "-f", config.docker.compose_file, "pull"])
    up_command = [
        "docker",
        "compose",
        "-f",
        config.docker.compose_file,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "90",
    ]
    if image.local_build:
        up_command.append("--build")
    commands.extend(
        [
            up_command,
            ["docker", "compose", "-f", config.docker.compose_file, "ps", "sftpwarden"],
        ]
    )
    return DeployPlan(commands=commands)


def _remote_local_sync_deploy_plan(context: ContextEntry) -> DeployPlan:
    """Build the sync and Compose plan for a remote local-sync context."""
    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    if not context.root or not context.config:
        raise ContextError(f"Remote local-sync context {context.name} is missing local settings.")
    compose_path = Path(context.root) / _remote_compose_filename(context)
    files = required_sync_files(
        Path(context.root),
        config_path=Path(context.config),
        compose_path=compose_path,
        require_exists=False,
    )
    mkdir_command = f"mkdir -p {remote_path_expression(context.remote.remote_root)}"
    transfer_commands = [remote_rsync_command(files, context.remote, root=Path(context.root))]
    # macOS ships openrsync, whose argument protection differs from GNU rsync.
    # Use the native OpenSSH file transport on both desktop platforms.
    if system_is("Windows") or system_is("Darwin"):
        transfer_commands = []
        for path in files:
            relative = path.relative_to(Path(context.root)).as_posix()
            destination = f"{context.remote.remote_root.rstrip('/')}/{relative}"
            parent = destination.rsplit("/", 1)[0]
            transfer_commands.extend(
                [
                    remote_shell_command(
                        context.remote, f"mkdir -p {remote_path_expression(parent)}"
                    ),
                    scp_upload_command(context.remote, path, destination),
                ]
            )
    return DeployPlan(
        commands=[
            remote_shell_command(context.remote, mkdir_command),
            *transfer_commands,
            remote_shell_command(context.remote, remote_compose_command(context.remote, "pull")),
            remote_shell_command(
                context.remote,
                remote_compose_command(
                    context.remote, "up -d --force-recreate --wait --wait-timeout 90"
                ),
            ),
            remote_shell_command(
                context.remote,
                remote_compose_command(context.remote, "ps sftpwarden"),
            ),
        ]
    )


def _remote_only_deploy_plan(context: ContextEntry) -> DeployPlan:
    """Build the in-place Compose plan for a remote-only context."""
    if not context.remote:
        raise ContextError(f"Remote context {context.name} is missing remote settings.")
    remote = context.remote
    relative = _remote_config_relative(remote)
    image = remote_preparation_image()
    common = ["--root=/project", f"--config={relative}", f"--compose-file={remote.compose_file}"]
    bootstrap_args = shlex.join(
        [
            image,
            "-m",
            "sftpwarden.remote.preparation",
            "bootstrap",
            *common,
            "--image",
            image,
        ]
    )
    bootstrap = (
        f"cd {remote_path_expression(remote.remote_root)} && "
        'sftpwarden_mount_source=$(printf \'%s\' "$PWD" | sed \'s/"/""/g\') && '
        'docker run --rm --network none --user "$(id -u):$(id -g)" '
        '--mount "type=bind,\\"source=$sftpwarden_mount_source\\",target=/project" '
        f'--entrypoint python {bootstrap_args} --host-root "$PWD" --owner "$(id -u):$(id -g)"'
    )
    prepare_base = f"docker compose --project-directory . -f {shlex.quote(PREPARATION_FILE)}"
    apply = f"cd {remote_path_expression(remote.remote_root)} && {prepare_base} run --rm -T prepare"
    complete_args = shlex.join(["-m", "sftpwarden.remote.preparation", "complete", *common])
    complete = (
        f"cd {remote_path_expression(remote.remote_root)} && "
        f"{prepare_base} run --rm -T --entrypoint python prepare {complete_args}"
    )
    return DeployPlan(
        commands=[
            remote_shell_command(remote, f"test -f {remote_path_expression(remote.remote_config)}"),
            remote_shell_command(remote, bootstrap),
            remote_shell_command(remote, apply),
            remote_shell_command(remote, remote_compose_command(remote, "pull")),
            remote_shell_command(
                remote,
                remote_compose_command(remote, "up -d --force-recreate --wait --wait-timeout 90"),
            ),
            remote_shell_command(remote, remote_compose_command(remote, "ps sftpwarden")),
            remote_shell_command(remote, complete),
        ]
    )


def remote_preparation_image() -> str:
    """Use the host CLI release's preparation code, independent of the old runtime."""
    return f"{GHCR_RUNTIME_IMAGE_REPOSITORY}:{get_version()}"


def _remote_config_relative(remote: RemoteEndpoint) -> str:
    try:
        relative = (
            PurePosixPath(remote.remote_config)
            .relative_to(PurePosixPath(remote.remote_root))
            .as_posix()
        )
    except ValueError:
        raise ContextError("Remote configuration must be a file within remote_root.") from None
    validate_relative_config(relative)
    if (
        not remote.compose_file.strip()
        or remote.compose_file in {".", ".."}
        or any(char in remote.compose_file for char in "/\\:\0\n\r")
    ):
        raise ContextError("Remote Compose must use a filename within remote_root.")
    return relative


def required_sync_files(
    root: Path, *, config_path: Path, compose_path: Path, require_exists: bool = True
) -> list[Path]:
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
    if config.provider.type in FILE_PROVIDER_TYPES:
        files.insert(1, provider_local_path(root, config))
    for path in files:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
        has_excluded_part = any(part in NEVER_SYNC_NAMES for part in relative_parts)
        if path.name in NEVER_SYNC_NAMES or has_excluded_part:
            raise RuntimeError(f"Refusing to sync excluded path: {path}")
        if require_exists and not path.exists():
            raise RuntimeError(f"Required deploy file does not exist: {path}")
    return files


def prepare_compose_deployment(context: ContextEntry) -> None:
    """Apply the explicit local input migration and render deployment artifacts."""
    if not context.root or not context.config:
        return
    remote_filename = (
        _remote_compose_filename(context) if context.type == ContextType.REMOTE else None
    )
    config = migrate_provider_layout(Path(context.root), Path(context.config))
    if remote_filename is not None:
        config = config.model_copy(deep=True)
        config.docker.compose_file = remote_filename
    write_compose(config, context.root, allow_local_build=context.type == ContextType.LOCAL)
    if context.type == ContextType.REMOTE and config.remote.include_env:
        from sftpwarden.remote.environment import deliver_environment

        deliver_environment(context)


def _remote_compose_filename(context: ContextEntry) -> str:
    """Validate a portable generated artifact before changing any project input."""
    if not context.remote or not context.config:
        raise ContextError(
            "Remote local-sync deployment requires remote settings and local config."
        )
    filename = context.remote.compose_file
    if (
        not filename.strip()
        or filename in {".", ".."}
        or any(char in filename for char in "/\\:\0")
    ):
        raise ContextError(
            "Remote local-sync Compose must use a filename within remote_root.",
            suggestion="Set remote.compose_file to a filename such as docker-compose.yml.",
        )
    config = load_config(context.config)
    reserved = {Path(context.config).name, "sftpwarden.yaml", "provider", ".sftpwarden"}
    reserved.update(NEVER_SYNC_NAMES)
    if config.provider.type in FILE_PROVIDER_TYPES:
        reserved.add(Path(config.provider.path).name)
    if filename in reserved:
        raise ContextError("Remote Compose filename conflicts with a project input file.")
    return filename


def complete_compose_deployment(context: ContextEntry) -> None:
    """Allow provider writes once the explicit mount transition is ready."""
    if context.root and context.config:
        complete_provider_layout(Path(context.root), load_config(context.config))


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

    # Preserve the legacy runner/output contract, but keep preparation, ownership
    # and activation in the same service used by the public CLI.
    from sftpwarden.services.deploy import apply_deployment_plan

    if runner is None:
        apply_deployment_plan(context)
    else:

        def adapted_runner(args: list[str], *, cwd: str | None = None) -> CommandResult:
            command_runner(args, cwd=cwd if context.type == ContextType.LOCAL else None)
            return CommandResult(args=args, returncode=0, stdout="", stderr="")

        apply_deployment_plan(context, runner=adapted_runner)
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
        message="Deploy command failed; private output has been withheld.",
        fallback_suggestion="Inspect the selected runtime and deployment configuration privately.",
        private_output=True,
    )
