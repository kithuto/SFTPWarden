"""Publish an immutable version index, advancing latest only in the final release job."""

from __future__ import annotations

import argparse
import json

from promote_image import remote_manifest, run


def identities(document: dict | None) -> dict[str, str]:
    """Reject missing, duplicate or unexpected platforms in a release index."""
    if document is None:
        return {}
    result = {}
    for item in document.get("manifests", []):
        platform = item.get("platform", {})
        arch = platform.get("architecture")
        if platform.get("os") != "linux" or arch not in {"amd64", "arm64"} or arch in result:
            raise RuntimeError("Release index contains an unverified or duplicate platform.")
        result[arch] = item["digest"]
    return result


def publish(repository: str, version: str, *, latest: bool = False) -> None:
    """Require both tested native architectures and refuse conflicting version manifests."""
    references = [f"{repository}:{version}-{arch}" for arch in ("amd64", "arm64")]
    digests = [
        json.loads(
            run(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "inspect",
                    "--format",
                    "{{json .Manifest}}",
                    reference,
                ]
            )
        )["digest"]
        for reference in references
    ]
    if len(set(digests)) != 2:
        raise RuntimeError("Architecture candidates must identify two distinct images.")
    expected = dict(zip(("amd64", "arm64"), digests, strict=True))
    for arch, reference in zip(expected, references, strict=True):
        image = json.loads(
            run(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "inspect",
                    "--format",
                    "{{json .Image}}",
                    reference,
                ]
            )
        )
        if image.get("architecture") != arch or image.get("os") != "linux":
            raise RuntimeError("Versioned image does not match its tested native platform.")
    versioned = f"{repository}:{version}"
    current = remote_manifest(versioned)
    if current is not None and identities(current) != expected:
        raise RuntimeError("Versioned architecture index already exists with different images.")
    if current is None:
        run(
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "--tag",
                versioned,
                *[f"{repository}@{digest}" for digest in digests],
            ]
        )
    published = remote_manifest(versioned)
    if identities(published) != expected:
        raise RuntimeError("Published version index did not verify.")
    if latest:
        run(
            ["docker", "buildx", "imagetools", "create", "--tag", repository + ":latest", versioned]
        )
        observed = remote_manifest(repository + ":latest")
        if identities(observed) != expected:
            raise RuntimeError(
                "Latest index did not verify; release completion requires inspection."
            )


def main() -> None:
    """Promote explicit versioned architecture references without rebuilding them."""
    parser = argparse.ArgumentParser()
    parser.add_argument("repository")
    parser.add_argument("version")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    publish(args.repository, args.version, latest=args.latest)


if __name__ == "__main__":
    main()
