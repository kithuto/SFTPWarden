from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import TypedDict

import yaml

from sftpwarden.config import RemoteStorage
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    ContextType,
    load_registry,
    save_registry,
)
from sftpwarden.remote.ssh import (
    explicit_ssh_key_path,
    uses_default_ssh_identity,
)
from sftpwarden.utils._version import get_version
from sftpwarden.utils.constants import CONFIG_FILENAME
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.paths import app_home, expand_path, source_root
from sftpwarden.watcher.base import BaseWatcher, WatcherImageReference, WatcherInstallMode
from sftpwarden.watcher.registry import register_watcher


class DockerComposeMount(TypedDict):
    """Docker Compose long-syntax bind mount."""

    type: str
    source: str
    target: str
    read_only: bool


DEFAULT_LOCAL_WATCHER_IMAGE = "sftpwarden-watcher:local"
GHCR_WATCHER_IMAGE_REPOSITORY = "ghcr.io/kithuto/sftpwarden-watcher"
SOURCE_ROOT = source_root()
LOCAL_WATCHER_DOCKERFILE = SOURCE_ROOT / "docker" / "watcher" / "Dockerfile"
CONTAINER_WATCHER_HOME = "/var/lib/sftpwarden-watcher"
CONTAINER_PROJECTS_ROOT = "/workspace"
CONTAINER_SSH_SOURCE_DIR = "/run/sftpwarden-watcher/ssh"
CONTAINER_SSH_WORK_DIR = "/tmp/sftpwarden-watcher/ssh"  # noqa: S108
CONTAINER_SSH_TMPFS = CONTAINER_SSH_WORK_DIR.rsplit("/", 1)[0]
CONTAINER_KNOWN_HOSTS_SOURCE = "/run/sftpwarden-watcher/known_hosts"


def watcher_image_reference(
    image: str | None = None, *, allow_local_build: bool = True
) -> WatcherImageReference:
    """Resolve the Docker watcher image for source checkouts and packaged installs."""
    if image:
        return WatcherImageReference(image=image)
    if allow_local_build and LOCAL_WATCHER_DOCKERFILE.exists():
        return WatcherImageReference(
            image=DEFAULT_LOCAL_WATCHER_IMAGE,
            build={
                "context": str(SOURCE_ROOT),
                "dockerfile": "docker/watcher/Dockerfile",
            },
        )
    return WatcherImageReference(
        image=f"{GHCR_WATCHER_IMAGE_REPOSITORY}:{get_version()}",
        pull_before_up=True,
    )


def docker_watcher_remote_contexts() -> list[ContextEntry]:
    """Return remote local-sync contexts relevant to Docker watcher."""
    registry = load_registry()
    return [
        context
        for context in registry.contexts.values()
        if context.type == ContextType.REMOTE and context.storage == "local-sync" and context.remote
    ]


def docker_watcher_contexts_path() -> Path:
    """Return the Docker-specific rewritten context registry path."""
    return app_home() / "watcher" / "docker-contexts.toml"


def docker_context_slug(context: ContextEntry) -> str:
    """Return a stable container-safe segment for a context."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", context.name).strip("-") or "context"
    digest = hashlib.sha256(context.name.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def docker_context_root(context: ContextEntry) -> str:
    """Return the container project root for a context."""
    return f"{CONTAINER_PROJECTS_ROOT}/{docker_context_slug(context)}"


def docker_context_config_path(context: ContextEntry) -> str:
    """Return the container config path for a context."""
    host_root = expand_path(context.root)
    host_config = expand_path(context.config)
    try:
        relative_config = host_config.resolve().relative_to(host_root.resolve())
    except ValueError:
        relative_config = Path(CONFIG_FILENAME)
    return f"{docker_context_root(context)}/{relative_config.as_posix()}"


def docker_context_key_path(context: ContextEntry) -> str:
    """Return the container SSH key path used by a rewritten context."""
    return f"{CONTAINER_SSH_WORK_DIR}/{docker_context_slug(context)}/identity"


def docker_bind_mount(
    source: str | Path,
    target: str,
    *,
    read_only: bool = True,
) -> DockerComposeMount:
    """Return a Docker Compose bind mount in long syntax."""
    return {
        "type": "bind",
        "source": str(source),
        "target": target,
        "read_only": read_only,
    }


def unique_mounts(mounts: list[DockerComposeMount]) -> list[DockerComposeMount]:
    """Return unique Compose mounts while preserving order."""
    seen: set[tuple[object, object, object]] = set()
    unique: list[DockerComposeMount] = []
    for mount in mounts:
        key = (mount.get("type"), mount.get("source"), mount.get("target"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(mount)
    return unique


def docker_watcher_project_volumes() -> list[DockerComposeMount]:
    """Return Docker bind mounts for watched local project roots."""
    return [
        docker_bind_mount(expand_path(context.root), docker_context_root(context))
        for context in docker_watcher_remote_contexts()
        if context.root
    ]


def docker_watcher_ssh_volumes() -> list[DockerComposeMount]:
    """Return SSH-related Docker bind mounts for watcher contexts."""
    volumes: list[DockerComposeMount] = []
    for context in docker_watcher_remote_contexts():
        remote = context.remote
        if remote is None:
            continue
        if uses_default_ssh_identity(remote.ssh_key):
            raise ContextError(
                f"Docker watcher cannot use the host default SSH identity for {context.name}.",
                suggestion=(
                    "Use a native watcher for host SSH config/agent support, or register "
                    "the context with --ssh-key /path/to/a/dedicated/key."
                ),
            )
        key_path = explicit_ssh_key_path(remote.ssh_key)
        if key_path is None or not key_path.exists():
            raise ContextError(
                f"Docker watcher SSH key not found for {context.name}: {key_path}",
                suggestion="Use an existing dedicated deployment key with --ssh-key.",
            )
        key_source_target = f"{CONTAINER_SSH_SOURCE_DIR}/{docker_context_slug(context)}/identity"
        volumes.append(docker_bind_mount(key_path, key_source_target))
    known_hosts = expand_path("~/.ssh/known_hosts")
    if volumes and known_hosts.exists():
        volumes.append(docker_bind_mount(known_hosts, CONTAINER_KNOWN_HOSTS_SOURCE))
    return volumes


def docker_watcher_container_registry() -> ContextRegistry:
    """Return a context registry rewritten for Linux paths inside the Docker watcher."""
    contexts: dict[str, ContextEntry] = {}
    for context in docker_watcher_remote_contexts():
        if not context.root or not context.config or not context.remote:
            continue
        remote = context.remote.model_copy(update={"ssh_key": docker_context_key_path(context)})
        contexts[context.name] = context.model_copy(
            update={
                "root": docker_context_root(context),
                "config": docker_context_config_path(context),
                "storage": RemoteStorage.LOCAL_SYNC,
                "remote": remote,
            }
        )
    registry = load_registry()
    default = registry.default if registry.default in contexts else next(iter(contexts), None)
    return ContextRegistry(default=default, contexts=contexts)


def write_docker_watcher_contexts() -> Path:
    """Write the Docker-specific context registry and return its path."""
    target = docker_watcher_contexts_path()
    save_registry(docker_watcher_container_registry(), path=target)
    return target


def render_docker_watcher_compose(*, image: str | None = None) -> str:
    """Render Docker Compose YAML for the watcher."""
    volumes = [
        docker_bind_mount(
            docker_watcher_contexts_path(),
            f"{CONTAINER_WATCHER_HOME}/contexts.toml",
        ),
        *docker_watcher_project_volumes(),
        *docker_watcher_ssh_volumes(),
    ]
    image_reference = watcher_image_reference(image)
    service = {
        "image": image_reference.image,
        "environment": {"SFTPWARDEN_HOME": CONTAINER_WATCHER_HOME},
        "volumes": unique_mounts(volumes),
        "tmpfs": [CONTAINER_SSH_TMPFS, "/root/.ssh"],
        "restart": "unless-stopped",
        "read_only": True,
        "security_opt": ["no-new-privileges:true"],
    }
    if image_reference.build:
        service["build"] = image_reference.build
    return yaml.safe_dump({"services": {"sftpwarden-watcher": service}}, sort_keys=False)


def docker_watcher_compose_path() -> Path:
    """Return the generated Docker watcher compose path."""
    return app_home() / "watcher" / "docker-compose.yml"


@register_watcher
class DockerWatcher(BaseWatcher):
    """Watcher backend managed by Docker Compose."""

    mode = WatcherInstallMode.DOCKER
    auto_priority = 1000
    native_scheduler = False
    accepts_image = True

    @classmethod
    def is_supported(cls) -> bool:
        """Return whether Docker is available on the current host."""
        return shutil.which("docker") is not None

    @classmethod
    def path(cls) -> Path:
        """Return the generated Docker Compose file path."""
        return docker_watcher_compose_path()

    @classmethod
    def render(cls, *, image: str | None = None) -> str:
        """Render the Docker Compose watcher configuration."""
        return render_docker_watcher_compose(image=image)

    @classmethod
    def commands(cls, *, image: str | None = None) -> list[list[str]]:
        """Return Docker Compose commands that activate the watcher."""
        image_reference = watcher_image_reference(image)
        compose_command = ["docker", "compose", "-f", str(docker_watcher_compose_path())]
        commands = []
        if image_reference.pull_before_up:
            commands.append([*compose_command, "pull"])
        if image_reference.local_build:
            commands.append([*compose_command, "up", "-d", "--build"])
        else:
            commands.append([*compose_command, "up", "-d"])
        return commands

    @classmethod
    def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
        """Return the Docker Compose command that stops the watcher."""
        compose_path = path or docker_watcher_compose_path()
        return [["docker", "compose", "-f", str(compose_path), "down"]]

    @classmethod
    def write(cls, *, image: str | None = None) -> Path:
        """Write Docker watcher contexts and Compose configuration."""
        write_docker_watcher_contexts()
        return super().write(image=image)
