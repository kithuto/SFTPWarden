"""Bounded public release discovery, independently of deployment operations."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import app_home
from sftpwarden.utils.persistence import atomic_bytes, file_lock, reject_symlink

GITHUB_RELEASES = "https://api.github.com/repos/kithuto/SFTPWarden/releases?per_page=100"
PYPI_PROJECT = "https://pypi.org/pypi/sftpwarden/json"


def stable_version(value: str) -> Version:
    """Accept only public stable package versions, never URLs or requirements."""
    try:
        parsed = Version(value)
    except InvalidVersion:
        raise SFTPWardenError("Invalid SFTPWarden release version.") from None
    if parsed.is_prerelease or parsed.is_devrelease or parsed.local or parsed.epoch:
        raise SFTPWardenError("Only stable public SFTPWarden releases are supported.")
    return parsed


def select_release(packages: dict[str, Any], releases: list[Any], target: str | None) -> str:
    """Require compatible non-yanked files and a completed public GitHub release."""
    if packages["info"]["name"].lower() != "sftpwarden":
        raise ValueError("Unexpected package identity")
    requested = stable_version(target) if target else None
    python = ".".join(str(part) for part in sys.version_info[:3])
    completed: set[Version] = set()
    for release in releases:
        if release.get("draft") or release.get("prerelease") or not release.get("published_at"):
            continue
        try:
            version = stable_version(release["tag_name"].removeprefix("v"))
        except SFTPWardenError:
            continue
        names = {asset["name"] for asset in release.get("assets", [])}
        # Legacy releases precede artifact inventories; newer releases must publish
        # the verified inventory used by the coordinated publication gate.
        if version >= Version("1.3.1") and "artifact-manifest.json" not in names:
            continue
        completed.add(version)
    candidates = []
    for text, files in packages["releases"].items():
        try:
            version = stable_version(text)
        except SFTPWardenError:
            continue
        if version not in completed or (requested is not None and version != requested):
            continue
        if any(
            not item.get("yanked", False)
            and item.get("packagetype") in {"bdist_wheel", "sdist"}
            and SpecifierSet(item.get("requires_python") or "").contains(python)
            for item in files
        ):
            candidates.append(version)
    if not candidates:
        raise SFTPWardenError("No completed stable release compatible with this Python was found.")
    return str(max(candidates))


def cache_path() -> Path:
    """Return the private advisory cache location."""
    return app_home() / "updates" / "releases.json"


def _cached(path: Path, now: float) -> dict[str, Any] | None:
    try:
        reject_symlink(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        age = now - data["checked_at"]
        ttl = 86400 if data["status"] == "available" else 3600
        if 0 <= age < ttl and data.get("python") == list(sys.version_info[:3]):
            if data["status"] == "available":
                stable_version(data["version"])
            return dict(data)
    except (OSError, ValueError, TypeError, KeyError, SFTPWardenError):
        pass
    return None


def discover_release(*, fresh: bool = False, target: str | None = None) -> dict[str, Any]:
    """Run network I/O in a killable worker, bounding DNS and slow responses too."""
    if target:
        target = str(stable_version(target))
    path = cache_path()
    now = time.time()
    if not fresh and target is None and (cached := _cached(path, now)):
        return cached
    try:
        with file_lock(path, timeout=0.05):
            if not fresh and target is None and (cached := _cached(path, now)):
                return cached
            result: dict[str, Any] = {
                "status": "unavailable",
                "version": None,
                "checked_at": now,
                "python": list(sys.version_info[:3]),
            }
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "sftpwarden.services.release_fetch",
                        *([target] if target else []),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=15 if fresh else 1.7,
                    check=False,
                )
                if completed.returncode == 0:
                    fetched = json.loads(completed.stdout)
                    version = str(stable_version(fetched["version"]))
                    if target is not None and version != target:
                        raise ValueError("Release target mismatch")
                    if "compatibility" in fetched:
                        from sftpwarden.services.update_compatibility import validate_compatibility

                        result["compatibility"] = validate_compatibility(
                            fetched["compatibility"], version
                        )
                    result.update(status="available", version=version)
            except (
                OSError,
                subprocess.TimeoutExpired,
                ValueError,
                KeyError,
                TypeError,
                SFTPWardenError,
            ):
                pass
            if target is None:
                atomic_bytes(path, json.dumps(result).encode())
            return result
    except (OSError, TimeoutError):
        return {"status": "unavailable", "version": None}
