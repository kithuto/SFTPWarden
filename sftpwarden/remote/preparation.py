"""Prepare remote-only projects inside the matching CLI's disposable image."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml

from sftpwarden.config import (
    FILE_PROVIDER_TYPES,
    DeployTarget,
    SFTPWardenConfig,
    load_config,
    write_config,
)
from sftpwarden.contexts import local_context
from sftpwarden.render.compose import compose_model
from sftpwarden.security.environment import dsn_environment_names
from sftpwarden.services.provider_layout import complete_provider_layout, migrate_provider_layout
from sftpwarden.services.provider_schema import reconcile_provider_schema
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import ProviderError, SFTPWardenError
from sftpwarden.utils.images import official_image
from sftpwarden.utils.persistence import atomic_bytes, reject_symlink

PREPARATION_FILE = ".sftpwarden/remote-preparation.yml"
CONTAINER_ROOT = "/project"


def validate_relative_config(value: str) -> PurePosixPath:
    """Require an actual config within the explicitly selected project directory."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."} or "\\" in value:
        raise ProviderError("Remote configuration must be a file within remote_root.")
    if any(char in value for char in "\0\n\r"):
        raise ProviderError("Remote configuration path contains unsupported control characters.")
    return path


def preparation_inputs(
    root: Path,
    relative_config: str,
    compose_file: str,
) -> tuple[Path, SFTPWardenConfig]:
    """Validate both output and inputs before publishing any project changes."""
    relative = validate_relative_config(relative_config)
    config_path = root.joinpath(*relative.parts)
    reject_symlink(root)
    for parent in config_path.relative_to(root).parents:
        reject_symlink(root / parent)
    reject_symlink(config_path)
    config = load_config(config_path)
    if config.deploy.target != DeployTarget.COMPOSE:
        raise ProviderError("Remote-only container preparation supports Docker Compose deployment.")
    if (
        not compose_file.strip()
        or compose_file in {".", ".."}
        or any(char in compose_file for char in "/\\:\0\n\r")
    ):
        raise ProviderError("Remote Compose must use a filename within remote_root.")
    reserved = {
        ".env",
        ".git",
        "data",
        "state",
        "host_keys",
        "provider",
        ".sftpwarden",
        "sftpwarden.yaml",
    }
    if len(relative.parts) == 1:
        reserved.add(relative.name)
    if config.provider.type in FILE_PROVIDER_TYPES:
        reserved.add(PurePosixPath(config.provider.path).name)
    if compose_file in reserved:
        raise ProviderError("Remote Compose filename conflicts with a project input file.")
    reject_symlink(root / ".sftpwarden")
    reject_symlink(root / compose_file)
    return config_path, config


def bootstrap_preparation(
    root: Path,
    *,
    relative_config: str,
    compose_file: str,
    host_root: str,
    image: str,
    owner: str,
) -> None:
    """Render an isolated helper with only the DSN variables needed for migration.

    Compose resolves references using the remote operator's environment and
    project .env. Neither this file nor the SSH command contains resolved values.
    No host Python installation or Docker socket inside the helper is required.
    """
    _, config = preparation_inputs(root, relative_config, compose_file)
    command = [
        "python",
        "-m",
        "sftpwarden.remote.preparation",
        "apply",
        f"--root={CONTAINER_ROOT}",
        f"--config={relative_config}",
        f"--compose-file={compose_file}",
    ]
    model = {
        "name": "sftpwarden-preparation",
        "services": {
            "prepare": {
                "image": image,
                "user": owner,
                "entrypoint": command,
                "network_mode": "bridge",
                "volumes": [{"type": "bind", "source": host_root, "target": CONTAINER_ROOT}],
                "environment": {
                    name: f"${{{name}:?Required provider environment variable is missing}}"
                    for name in dsn_environment_names(config.provider.dsn or "")
                },
            }
        },
    }
    atomic_bytes(root / PREPARATION_FILE, yaml.safe_dump(model, sort_keys=False).encode())


def apply_preparation(root: Path, *, relative_config: str, compose_file: str) -> None:
    """Reconcile schemas, migrate file mounts, and render the selected Compose file."""
    config_path, config = preparation_inputs(root, relative_config, compose_file)
    if official_image(config.docker.image):
        config.docker.image = f"ghcr.io/kithuto/sftpwarden:{get_version()}"
        write_config(config_path, config)
    entry = local_context(config.project.name, root, config.provider.type)
    entry.config = str(config_path)
    reconcile_provider_schema(entry, config)
    config = migrate_provider_layout(root, config_path)
    model = compose_model(config, root, allow_local_build=False)
    # The source filename is a host path interpreted by Compose in the project,
    # while the runtime continues to use its stable internal config path.
    model["services"]["sftpwarden"]["volumes"][0] = {
        "type": "bind",
        "source": f"./{relative_config}",
        "target": "/etc/sftpwarden/sftpwarden.yaml",
        "read_only": True,
    }
    content = yaml.safe_dump(model, sort_keys=False).encode()
    target = root / compose_file
    if target.exists() and target.read_bytes() != content:
        backup = root / ".sftpwarden" / "backups" / f"compose-{uuid4().hex}.yaml"
        reject_symlink(backup.parent)
        atomic_bytes(backup, target.read_bytes())
    atomic_bytes(target, content)


def finish_preparation(root: Path, *, relative_config: str, compose_file: str) -> None:
    """Release provider edits only after Compose has reported runtime readiness."""
    _, config = preparation_inputs(root, relative_config, compose_file)
    complete_provider_layout(root, config)
    (root / PREPARATION_FILE).unlink(missing_ok=True)


def main() -> None:
    """Run the bounded preparation operation selected by the host CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["bootstrap", "apply", "complete"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--host-root")
    parser.add_argument("--image")
    parser.add_argument("--owner")
    args = parser.parse_args()
    try:
        if args.phase == "bootstrap":
            if not args.host_root or not args.image or not args.owner:
                parser.error("bootstrap requires --host-root, --image and --owner")
            bootstrap_preparation(
                args.root,
                relative_config=args.config,
                compose_file=args.compose_file,
                host_root=args.host_root,
                image=args.image,
                owner=args.owner,
            )
        elif args.phase == "apply":
            apply_preparation(
                args.root,
                relative_config=args.config,
                compose_file=args.compose_file,
            )
        else:
            finish_preparation(
                args.root,
                relative_config=args.config,
                compose_file=args.compose_file,
            )
    except (SFTPWardenError, OSError):
        # Config/journal/driver exceptions can contain private input in causes.
        raise SystemExit(
            "Remote preparation failed; inspect the private project recovery files."
        ) from None


if __name__ == "__main__":
    main()
