"""Record and verify the exact bytes promoted by the release dependency graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

MANIFEST = "artifact-manifest.json"


def inventory(root: Path) -> dict[str, dict[str, object]]:
    """Hash only regular artifact files, rejecting links and empty artifact sets."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Artifact directories cannot contain symbolic links.")
        if path.is_dir() or path == root / MANIFEST:
            continue
        if not path.is_file():
            raise ValueError("Artifact directories can only contain regular files.")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        result[path.relative_to(root).as_posix()] = {"sha256": digest, "size": path.stat().st_size}
    if not result:
        raise ValueError("No candidate artifacts were found.")
    return result


def record(root: Path, commit: str, version: str) -> None:
    """Record candidate provenance once; never silently update an existing manifest."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full promotion commit SHA is required.")
    document = {"format": 1, "commit": commit, "version": version, "files": inventory(root)}
    with (root / MANIFEST).open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")


def verify(root: Path, commit: str) -> dict[str, object]:
    """Refuse changed, missing or additional bytes and artifacts from another commit."""
    path = root / MANIFEST
    if path.is_symlink():
        raise ValueError("Candidate manifest cannot be a symbolic link.")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != 1 or document.get("commit") != commit:
        raise ValueError("Candidate provenance does not match this promotion commit.")
    if document.get("files") != inventory(root):
        raise ValueError("Candidate bytes differ from the recorded artifact inventory.")
    return document


def main() -> None:
    """Run explicit record/verify actions without rebuilding any artifacts."""
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["record", "verify"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version")
    args = parser.parse_args()
    if args.action == "record":
        if not args.version:
            parser.error("record requires --version")
        record(args.root, args.commit, args.version)
    else:
        verify(args.root, args.commit)


if __name__ == "__main__":
    main()
