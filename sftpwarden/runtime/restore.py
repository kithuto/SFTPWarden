"""Receive validated recovery archives in a disposable Linux helper."""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import tempfile
from pathlib import Path

from sftpwarden.services.backup_manifest import validate_backup_manifest
from sftpwarden.services.restore_target import inspect_empty_target, publish_fresh_files
from sftpwarden.utils.archives import MAX_ARCHIVE_BYTES, extract_private_archive
from sftpwarden.utils.errors import RuntimeError


def restore_empty(
    root: Path, *, include_data: bool, owner: tuple[int, int], protected_external: bool = False
) -> None:
    """Preflight the complete stream, then publish a separately verified empty project."""
    if root.parent != Path("/destination") or root.name in {"", ".", ".."}:
        raise RuntimeError("Recovery requires a dedicated directory below /destination.")
    if any(value < 0 or value >= 2**32 - 1 for value in owner):
        raise RuntimeError("Invalid recovery project owner.")
    with tempfile.TemporaryDirectory(prefix="sftpwarden-receive-") as temporary:
        staged = Path(temporary)
        packed = staged / "archive.tar.gz"
        count = 0
        with packed.open("xb") as output:
            os.chmod(packed, 0o600)
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                count += len(chunk)
                if count > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Recovery stream exceeds the supported size.")
                output.write(chunk)
        source = staged / "source"
        source.mkdir(mode=0o700)
        with tarfile.open(packed, "r:gz") as archive:
            headers = extract_private_archive(archive, source, private_staging=True)
        manifest = validate_backup_manifest(source, headers)
        entries = [
            name
            for name in manifest.entries
            if include_data or (name != "data" and not name.startswith("data/"))
        ]
        inspected = inspect_empty_target(
            str(root),
            manifest,
            source=source,
            entries=entries,
            protected_external=protected_external,
        )
        # Ownership is applied before publication, without traversing destination content.
        # Linux runtime ownership is restored from the separate validated metadata record.
        publish_fresh_files(inspected, source, entries, manifest, project_owner=owner)


def main() -> int:
    """Keep archive content and credentials out of transport diagnostics."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--gid", type=int, required=True)
    parser.add_argument("--include-data", action="store_true")
    parser.add_argument("--protected-external", action="store_true")
    args = parser.parse_args()
    try:
        restore_empty(
            args.root,
            include_data=args.include_data,
            owner=(args.uid, args.gid),
            protected_external=args.protected_external,
        )
        return 0
    except Exception:  # noqa: BLE001
        print(
            (
                "Fresh recovery failed. Inspect the destination before retrying; service "
                "activation is separate."
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
