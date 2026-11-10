"""Promote tested local image bytes without replacing a different versioned image."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

from registry_errors import manifest_missing


def run(command: list[str]) -> str:
    """Capture registry diagnostics privately and require a successful operation."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode:
        raise RuntimeError("Image promotion command failed; inspect registry access privately.")
    return result.stdout


def remote_manifest(reference: str) -> dict | None:
    """Distinguish an explicitly absent manifest from authentication/transport failures."""
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        if manifest_missing(result.stderr, reference):
            return None
        raise RuntimeError("Registry manifest inspection failed; absence was not established.")
    return json.loads(result.stdout)


def local_config_digest(source: str) -> str:
    """Resolve config identity for classic and containerd Docker image stores."""
    inspected = json.loads(run(["docker", "image", "inspect", "--format", "{{json .}}", source]))
    identity = inspected["Id"]
    # Containerd-backed stores can report the manifest digest as the image ID.
    # Compare the config bytes, which survive registry layer compression.
    if (inspected.get("Descriptor") or {}).get("digest") != identity:
        return identity
    with tempfile.TemporaryDirectory(prefix="sftpwarden-image-identity-") as directory:
        archive = Path(directory) / "image.tar"
        run(["docker", "image", "save", "--output", str(archive), source])
        with tarfile.open(archive) as saved:
            manifest = saved.extractfile("manifest.json")
            if manifest is None:
                raise RuntimeError("Saved candidate image has no manifest.")
            entries = json.load(manifest)
            if len(entries) != 1:
                raise RuntimeError("Expected exactly one candidate image in the saved archive.")
            config = saved.extractfile(entries[0]["Config"])
            if config is None:
                raise RuntimeError("Saved candidate image has no configuration.")
            with config:
                return "sha256:" + hashlib.file_digest(config, "sha256").hexdigest()


def promote(source: str, destination: str) -> None:
    """Publish only a missing architecture tag, then compare its config identity."""
    identity = local_config_digest(source)
    previous = remote_manifest(destination)
    if previous is not None:
        if previous.get("config", {}).get("digest") != identity:
            raise RuntimeError(
                "Versioned image already exists with different bytes; refusing overwrite."
            )
        return
    run(["docker", "tag", source, destination])
    run(["docker", "push", destination])
    published = remote_manifest(destination)
    if published is None or published.get("config", {}).get("digest") != identity:
        raise RuntimeError("Published image identity did not verify; stop promotion.")


def main() -> None:
    """Promote a loaded, separately verified candidate image."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    promote(args.source, args.destination)


if __name__ == "__main__":
    main()
