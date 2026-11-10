"""Promote tested local image bytes without replacing a different versioned image."""

from __future__ import annotations

import argparse
import json
import subprocess


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
        diagnostic = result.stderr.lower()
        if not any(word in diagnostic for word in ("denied", "unauthorized", "forbidden")) and (
            "manifest unknown" in diagnostic or "manifest_unknown" in diagnostic
        ):
            return None
        raise RuntimeError("Registry manifest inspection failed; absence was not established.")
    return json.loads(result.stdout)


def promote(source: str, destination: str) -> None:
    """Publish only a missing architecture tag, then compare its config identity."""
    identity = run(["docker", "image", "inspect", "--format", "{{.Id}}", source]).strip()
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
