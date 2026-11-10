"""Promote the tested chart archive without overwriting another archive at its version."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    """Hash the complete compressed chart bytes, not merely its version metadata."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pull(reference: str, version: str, root: Path) -> Path | None:
    """Treat only an explicit registry absence as a chart that can be published."""
    result = subprocess.run(
        ["helm", "pull", reference, "--version", version, "--destination", str(root)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        diagnostic = result.stderr.lower()
        if not any(word in diagnostic for word in ("unauthorized", "denied", "forbidden")) and any(
            word in diagnostic for word in ("manifest unknown", "manifest_unknown", "404 not found")
        ):
            return None
        raise RuntimeError("Chart registry inspection failed; absence was not established.")
    return root / f"sftpwarden-{version}.tgz"


def promote(archive: Path, repository: str, version: str) -> None:
    """Verify an existing chart or push and read back the exact missing candidate."""
    expected = digest(archive)
    with tempfile.TemporaryDirectory(prefix="sftpwarden-chart-promotion-") as directory:
        root = Path(directory)
        previous = pull(repository + "/sftpwarden", version, root)
        if previous is not None:
            if digest(previous) != expected:
                raise RuntimeError(
                    "Versioned chart exists with different bytes; refusing overwrite."
                )
            return
        result = subprocess.run(
            ["helm", "push", str(archive), repository],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Chart publication failed; inspect registry access privately.")
        published = pull(repository + "/sftpwarden", version, root)
        if published is None or digest(published) != expected:
            raise RuntimeError("Published chart bytes did not verify.")


def main() -> None:
    """Publish only the supplied verified chart archive."""
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("repository")
    parser.add_argument("version")
    args = parser.parse_args()
    promote(args.archive, args.repository, args.version)


if __name__ == "__main__":
    main()
