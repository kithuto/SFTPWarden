"""Refuse conflicting release tags/assets before any candidate promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote


def request(path: str) -> dict | None:
    """Use the workflow token privately; only 404 means absent."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    message = urllib.request.Request("https://api.github.com/" + path, headers=headers)
    try:
        with urllib.request.urlopen(message, timeout=30) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(
            "Release provenance lookup failed; absence was not established."
        ) from None


def check(
    root: Path, repository: str, version: str, commit: str, *, complete: bool = False
) -> None:
    """Never attach different bytes or a different commit to an existing version."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("An explicit GitHub owner/repository is required.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full candidate commit is required.")
    prefix = "repos/" + repository
    tag = quote("v" + version, safe="")
    reference = request(prefix + "/git/ref/tags/" + tag)
    if reference is not None:
        obj = reference["object"]
        for _ in range(8):
            if obj["type"] != "tag":
                break
            annotated = request(prefix + "/git/tags/" + obj["sha"])
            if annotated is None:
                raise RuntimeError("Existing annotated release tag could not be resolved.")
            obj = annotated["object"]
        if obj["type"] != "commit" or obj["sha"] != commit:
            raise RuntimeError("The version tag already names a different commit; stop promotion.")
    release = request(prefix + "/releases/tags/" + tag)
    if release is None:
        if complete:
            raise RuntimeError("The completed release could not be read back.")
        return
    if reference is None or release.get("draft") or release.get("prerelease"):
        raise RuntimeError("An existing release has unresolved candidate provenance.")
    paths = [
        *root.glob("*.whl"),
        *root.glob("*.tar.gz"),
        *root.glob("chart/*.tgz"),
        root / "artifact-manifest.json",
    ]
    expected = {}
    for path in paths:
        with path.open("rb") as stream:
            expected[path.name] = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    seen = set()
    for asset in release["assets"]:
        name = asset["name"]
        if name in seen or name not in expected or asset.get("digest") != expected[name]:
            raise RuntimeError(
                "Existing release assets differ or lack verified digests; stop promotion."
            )
        seen.add(name)
    if complete and seen != set(expected):
        raise RuntimeError("The release is missing candidate assets; completion was not verified.")


def main() -> None:
    """Check only; this command never creates a tag or a release."""
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    check(args.root, args.repository, args.version, args.commit, complete=args.complete)


if __name__ == "__main__":
    main()
