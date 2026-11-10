"""Verify PyPI immutability and stage only missing candidate distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path


class PublicationPending(ValueError):
    """Expected files are not visible yet; conflicting files are never retryable."""


def published_files(version: str) -> dict[str, str]:
    """Accept only an explicit 404 as an absent release."""
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/sftpwarden/{version}/json", timeout=30
        ) as response:  # noqa: S310
            document = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise
    return {item["filename"]: item["digests"]["sha256"] for item in document["urls"]}


def check(root: Path, version: str, *, output: Path | None = None) -> int:
    """Refuse conflicting published bytes and optionally copy distributions still missing."""
    published = published_files(version)
    wheels, sources = list(root.glob("*.whl")), list(root.glob("*.tar.gz"))
    files = wheels + sources
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Candidate requires exactly one wheel and one source distribution.")
    if set(published) - {path.name for path in files}:
        raise ValueError("Published version contains unexpected distributions; stop promotion.")
    missing = []
    for path in files:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        actual = published.get(path.name)
        if actual is not None and actual != digest:
            raise ValueError("Published PyPI artifact differs from the candidate; stop promotion.")
        if actual is None:
            missing.append(path)
    if output is None and missing:
        raise PublicationPending("Candidate distributions are not all published yet.")
    if output is not None:
        output.mkdir(exist_ok=False)
        for path in missing:
            shutil.copyfile(path, output / path.name)
    return len(missing)


def main() -> None:
    """Stage before publication or verify the complete published version afterward."""
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 300 or args.stage and args.wait_seconds:
        parser.error("Wait must be between 0 and 300 seconds and cannot accompany staging.")
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            missing = check(args.root, args.version, output=args.stage)
            break
        except PublicationPending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(5, remaining))
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"missing={missing}\n")


if __name__ == "__main__":
    main()
