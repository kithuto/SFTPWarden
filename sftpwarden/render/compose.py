from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sftpwarden.config import FILE_PROVIDER_TYPES, SFTPWardenConfig, provider_local_path
from sftpwarden.utils._version import get_version
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.paths import expand_path, source_root

DEFAULT_LOCAL_RUNTIME_IMAGE = "sftpwarden:local"
GHCR_RUNTIME_IMAGE_REPOSITORY = "ghcr.io/kithuto/sftpwarden"
SOURCE_ROOT = source_root()
LOCAL_RUNTIME_DOCKERFILE = SOURCE_ROOT / "docker" / "runtime" / "Dockerfile"


@dataclass(frozen=True)
class RuntimeImageReference:
    """Resolved runtime image and optional local build metadata."""

    image: str
    build: dict[str, str] | None = None

    @property
    def local_build(self) -> bool:
        """Return whether Docker Compose should build the image locally."""
        return self.build is not None


def runtime_image_reference(
    config: SFTPWardenConfig, *, allow_local_build: bool = True
) -> RuntimeImageReference:
    """Resolve the Docker runtime image for source checkouts and packaged installs."""
    if config.docker.image != DEFAULT_LOCAL_RUNTIME_IMAGE:
        return RuntimeImageReference(image=config.docker.image)
    if allow_local_build and LOCAL_RUNTIME_DOCKERFILE.exists():
        return RuntimeImageReference(
            image=DEFAULT_LOCAL_RUNTIME_IMAGE,
            build={
                "context": str(SOURCE_ROOT),
                "dockerfile": "docker/runtime/Dockerfile",
            },
        )
    return RuntimeImageReference(image=f"{GHCR_RUNTIME_IMAGE_REPOSITORY}:{get_version()}")


def compose_model(
    config: SFTPWardenConfig,
    project_root: str | Path = ".",
    *,
    allow_local_build: bool = True,
) -> dict:
    """Build a Docker Compose model for the runtime.

    Parameters
    ----------
    config
        Project config.
    project_root
        Local project root.
    allow_local_build
        Whether source checkouts should emit a local Docker build section.

    Returns
    -------
    dict
        Docker Compose model.
    """
    root = expand_path(project_root)
    image = runtime_image_reference(config, allow_local_build=allow_local_build)
    volumes = [
        f"./sftpwarden.yaml:{CONTAINER_CONFIG_PATH}:ro",
        "./data:/data",
        "./state:/var/lib/sftpwarden",
        "./host_keys:/etc/sftpwarden/host_keys",
    ]
    if config.provider.type in FILE_PROVIDER_TYPES:
        provider_path = provider_local_path(root, config)
        volumes.insert(1, f"./{provider_path.name}:{config.provider.path}:ro")
    healthcheck = config.healthcheck
    service = {
        "container_name": config.docker.container_name,
        "image": image.image,
        "ports": [f"{config.server.port}:22"],
        "environment": {"SFTPWARDEN_CONFIG": CONTAINER_CONFIG_PATH},
        "volumes": volumes,
        "restart": config.docker.restart,
        "read_only": False,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "SYS_CHROOT"],
        "healthcheck": {
            "test": [
                "CMD",
                "sftpwarden",
                "runtime",
                "health",
                "--config",
                CONTAINER_CONFIG_PATH,
            ],
            "interval": f"{healthcheck.interval_seconds}s",
            "timeout": f"{healthcheck.timeout_seconds}s",
            "retries": healthcheck.retries,
            "start_period": f"{healthcheck.start_period_seconds}s",
        },
    }
    if image.build:
        service["build"] = image.build
    return {"services": {"sftpwarden": service}}


def compose_text(
    config: SFTPWardenConfig,
    project_root: str | Path = ".",
    *,
    allow_local_build: bool = True,
) -> str:
    """Render Docker Compose YAML for the runtime.

    Parameters
    ----------
    config
        Project config.
    project_root
        Local project root.
    allow_local_build
        Whether source checkouts should emit a local Docker build section.

    Returns
    -------
    str
        Docker Compose YAML text.
    """
    return yaml.safe_dump(
        compose_model(config, project_root, allow_local_build=allow_local_build),
        sort_keys=False,
    )


def write_compose(
    config: SFTPWardenConfig,
    project_root: str | Path = ".",
    *,
    allow_local_build: bool = True,
) -> Path:
    """Write the runtime Docker Compose file.

    Parameters
    ----------
    config
        Project config.
    project_root
        Local project root.
    allow_local_build
        Whether source checkouts should emit a local Docker build section.

    Returns
    -------
    Path
        Written compose file path.
    """
    root = expand_path(project_root)
    target = root / config.docker.compose_file
    target.write_text(
        compose_text(config, root, allow_local_build=allow_local_build), encoding="utf-8"
    )
    return target
