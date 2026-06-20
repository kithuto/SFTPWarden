from __future__ import annotations

from pathlib import Path

import yaml

from sftpwarden.config import SFTPWardenConfig, provider_local_path
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.paths import expand_path


def compose_model(config: SFTPWardenConfig, project_root: str | Path = ".") -> dict:
    root = expand_path(project_root)
    provider_path = provider_local_path(root, config)
    volumes = [
        f"./sftpwarden.yaml:{CONTAINER_CONFIG_PATH}:ro",
        f"./{provider_path.name}:{config.provider.path}:ro",
        "./data:/data",
        "./state:/var/lib/sftpwarden",
        "./host_keys:/etc/sftpwarden/host_keys",
    ]
    return {
        "services": {
            "sftpwarden": {
                "container_name": config.docker.container_name,
                "image": config.docker.image,
                "build": {"context": "."},
                "ports": [f"{config.server.port}:22"],
                "environment": {"SFTPWARDEN_CONFIG": CONTAINER_CONFIG_PATH},
                "volumes": volumes,
                "restart": config.docker.restart,
                "read_only": False,
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "SYS_CHROOT"],
            }
        }
    }


def compose_text(config: SFTPWardenConfig, project_root: str | Path = ".") -> str:
    return yaml.safe_dump(compose_model(config, project_root), sort_keys=False)


def write_compose(config: SFTPWardenConfig, project_root: str | Path = ".") -> Path:
    root = expand_path(project_root)
    target = root / config.docker.compose_file
    target.write_text(compose_text(config, root), encoding="utf-8")
    return target
