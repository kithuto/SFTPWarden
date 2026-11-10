"""Capture mounted recovery material inside a disposable Linux helper."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from sftpwarden.config import (
    FILE_PROVIDER_TYPES,
    DeployTarget,
    ProviderType,
    SFTPWardenConfig,
    write_config,
)
from sftpwarden.render.compose import write_compose
from sftpwarden.runtime.core import RuntimeState
from sftpwarden.runtime.recovery import BLOCK_FILE, RECOVERY_FILE, validate_data_owner
from sftpwarden.security.environment import dsn_environment_names
from sftpwarden.services.backup import write_backup_archive
from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes
from sftpwarden.utils.sqlite import snapshot_sqlite


def capture(
    applied: Path,
    *,
    include_data: bool,
    topology: str = "compose-runtime",
    container_info: Path | None = None,
) -> None:
    """Stream applied config, provider policy and stopped-runtime mounts as one archive."""
    record = json.loads(applied.read_text(encoding="utf-8"))
    if record.get("format") != 1:
        raise RuntimeError("Unsupported applied runtime configuration.")
    config = SFTPWardenConfig.model_validate(record["config"])
    # Recovery files describe the captured runtime, not a second deployment hop.
    config.remote.enabled = False
    config.deploy.target = DeployTarget.COMPOSE
    if config.provider.type not in FILE_PROVIDER_TYPES:
        if container_info is None:
            raise RuntimeError(
                "Runtime database capture requires its selected connection environment."
            )
        info = json.loads(container_info.read_text(encoding="utf-8"))
        environment = dict(value.split("=", 1) for value in info["Config"]["Env"] if "=" in value)
        selected = {
            *dsn_environment_names(config.provider.dsn or ""),
            "PGSSLMODE",
            "PGSSLROOTCERT",
            "PGSSLCERT",
            "PGSSLKEY",
            "PGCONNECT_TIMEOUT",
        }
        for name in selected:
            if name in environment:
                os.environ[name] = environment[name]
            else:
                os.environ.pop(name, None)
    state_path = Path(config.server.state_dir) / "state.json"
    if not state_path.is_file():
        raise RuntimeError("Runtime identity state is missing; capture cannot be complete.")
    if any((state_path.parent / name).exists() for name in (RECOVERY_FILE, BLOCK_FILE)):
        raise RuntimeError("Finish the pending recovery before creating a complete runtime backup.")
    state = RuntimeState.load(state_path)
    if state.pending or not state.fingerprint:
        raise RuntimeError("Runtime reconciliation must finish before recovery capture.")
    sources = {
        "host_keys": Path(config.server.host_keys_dir),
        "state": Path(config.server.state_dir),
    }
    if include_data:
        sources["data"] = Path(config.server.data_dir)

    def ownership(path: Path, name: str) -> tuple[int, int]:
        parts = name.split("/")
        identity = state.users.get(parts[1]) if len(parts) >= 3 and parts[0] == "data" else None
        if identity is not None:
            # Host-backed filesystems can synthesize ownership for the calling UID.
            # Observe the same ownership the SFTP user sees, without reading payloads
            # or changing any files under that identity. Explicit root ownership is
            # still reported as root; inaccessible operator-only paths use root's view.
            groups = os.getgroups()
            try:
                os.setgroups([])
                os.setegid(identity.gid)
                os.seteuid(identity.uid)
                info = path.lstat()
                validate_data_owner(name, (info.st_uid, info.st_gid), state)
                return info.st_uid, info.st_gid
            except PermissionError:
                pass
            finally:
                os.seteuid(0)
                os.setegid(0)
                os.setgroups(groups)
        info = path.lstat()
        validate_data_owner(name, (info.st_uid, info.st_gid), state)
        return info.st_uid, info.st_gid

    with tempfile.TemporaryDirectory(prefix="sftpwarden-runtime-backup-") as directory:
        root = Path(directory)
        provider_entries = []
        if config.provider.type in FILE_PROVIDER_TYPES:
            original_provider = Path(config.provider.path)
            (root / "provider").mkdir(mode=0o700)
            copied_provider = root / "provider" / original_provider.name
            if config.provider.type == ProviderType.SQLITE:
                snapshot_sqlite(original_provider, copied_provider)
            else:
                atomic_bytes(copied_provider, original_provider.read_bytes())
            config.provider.path = f"{CONTAINER_PROVIDER_DATA_DIR}/{original_provider.name}"
            provider_entries.append(f"provider/{original_provider.name}")
        write_config(root / "sftpwarden.yaml", config)
        write_compose(config, root, allow_local_build=False)
        entries = [
            "sftpwarden.yaml",
            config.docker.compose_file,
            *provider_entries,
        ]
        entries.extend(sources)
        write_backup_archive(
            output_path=None,
            stream=sys.stdout.buffer,
            root=root,
            entries=entries,
            context_name=config.project.name,
            provider_type=config.provider.type.value,
            include_data=include_data,
            sources=sources,
            ownership=ownership,
            consistency="stopped-runtime; external-writers-paused-by-operator",
            topology=topology,
            applied_fingerprint=state.fingerprint,
        )


def main() -> int:
    """Keep errors out of the binary archive stream and redact provider details."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--include-data", action="store_true")
    parser.add_argument("--container-info", type=Path)
    parser.add_argument(
        "--topology",
        choices=["compose-runtime", "remote-compose-runtime", "kubernetes-runtime"],
        default="compose-runtime",
    )
    args = parser.parse_args()
    try:
        capture(
            args.applied,
            include_data=args.include_data,
            topology=args.topology,
            container_info=args.container_info,
        )
        return 0
    except Exception:  # noqa: BLE001
        print(
            "Runtime recovery capture failed; no complete archive was published.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
