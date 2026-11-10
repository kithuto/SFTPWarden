"""Restore into new PVCs; retain an incomplete marker after any partial publication."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, provider_local_path
from sftpwarden.runtime.recovery import BLOCK_FILE, apply_recovery_metadata, write_recovery_record
from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.services.restore_target import recovery_metadata
from sftpwarden.utils.archives import MAX_ARCHIVE_BYTES, check_source_tree, extract_private_archive
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes, sync_directory


def receive(*, include_data: bool) -> None:
    """Inspect all destinations before writing and verify Linux metadata before completion."""
    with tempfile.TemporaryDirectory(prefix="sftpwarden-pvc-recovery-") as directory:
        root = Path(directory)
        packed = root / "archive.tgz"
        count = 0
        with packed.open("xb") as output:
            packed.chmod(0o600)
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                count += len(chunk)
                if count > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Recovery stream exceeds the supported limit.")
                output.write(chunk)
        source = root / "source"
        source.mkdir(mode=0o700)
        with tarfile.open(packed, "r:gz") as archive:
            headers = extract_private_archive(archive, source, private_staging=True)
        manifest = validate_backup_manifest(source, headers)
        config = manifest.config
        entries = [
            name
            for name in manifest.entries
            if include_data or (name != "data" and not name.startswith("data/"))
        ]
        metadata = recovery_metadata(source, manifest, entries)
        if not {"state", "host_keys"}.issubset(entries) or not metadata:
            raise RuntimeError(
                "Cluster restoration requires retained runtime state and host identity."
            )
        provider = (
            provider_local_path(source, config)
            if config.provider.type in FILE_PROVIDER_TYPES
            else None
        )
        destinations = {
            "state": Path(config.server.state_dir),
            "host_keys": Path(config.server.host_keys_dir),
            "data": Path(config.server.data_dir),
        }
        if provider is not None:
            destinations["provider"] = Path(config.provider.path).parent
        if len({str(path) for path in destinations.values()}) != len(destinations):
            raise RuntimeError("Recovery mounts overlap.")
        for path in destinations.values():
            if not path.is_mount() or path.is_symlink():
                raise RuntimeError("Recovery destinations must be dedicated mounted volumes.")
            check_source_tree(path)
            if not path.is_dir() or any(path.iterdir()):
                raise RuntimeError("Every selected recovery volume must be empty.")
        write_recovery_record(source, metadata, config)
        blocker = destinations["state"] / BLOCK_FILE
        atomic_bytes(blocker, b'{"format":1,"phase":"restoring_new_volumes"}')
        completed = []
        try:
            for name in ("host_keys", "state", "data"):
                if name == "data" and not include_data:
                    continue
                original = source / name
                if not original.is_dir():
                    raise RuntimeError("Backup is missing a required recovery directory.")
                for child in original.iterdir():
                    destination = destinations[name] / child.name
                    if child.is_dir():
                        shutil.copytree(child, destination)
                    else:
                        shutil.copy2(child, destination)
                completed.append(name)
                atomic_bytes(
                    blocker,
                    json.dumps(
                        {"format": 1, "phase": "restoring_new_volumes", "completed": completed}
                    ).encode(),
                )
            if provider is not None:
                atomic_bytes(Path(config.provider.path), provider.read_bytes())
            for path in destinations.values():
                for member in path.rglob("*"):
                    if member.is_file():
                        with member.open("r+b") as stream:
                            os.fsync(stream.fileno())
                sync_directory(path)
            blocker.unlink()
            sync_directory(blocker.parent)
            apply_recovery_metadata(config)
        except Exception:
            atomic_bytes(
                blocker,
                json.dumps({"format": 1, "phase": "failed", "completed": completed}).encode(),
            )
            raise


def main() -> int:
    """Restore the selected recovery archive into isolated Linux claim mounts."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-data", action="store_true")
    args = parser.parse_args()
    try:
        receive(include_data=args.include_data)
        return 0
    except Exception:  # noqa: BLE001
        print(
            (
                "PVC recovery failed. Keep the incomplete volumes and recover separately "
                "before activation."
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
