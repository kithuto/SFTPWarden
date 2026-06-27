"""Clean local validation, build, documentation, and smoke-test artifacts."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = Path(tempfile.gettempdir())
DOCKER_SMOKE_IMAGES = ("sftpwarden:release-smoke", "sftpwarden-watcher:release-smoke")
WALK_EXCLUDED_DIRS = {
    ".git",
    ".tox",
    ".venv",
    "venv",
    "dist",
    "build",
    "docs/_build",
}


@dataclass
class CleanupResult:
    """Paths and images affected by one cleanup run."""

    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def add_removed(self, value: str) -> None:
        """Record one removed artifact."""
        self.removed.append(value)

    def add_skipped(self, value: str) -> None:
        """Record one skipped artifact."""
        self.skipped.append(value)


def artifact_paths(groups: set[str]) -> list[Path]:
    """Return artifact paths for the selected cleanup groups."""
    paths: list[Path] = []
    if "python" in groups:
        paths.extend(
            [
                ROOT / ".coverage",
                ROOT / "coverage.xml",
                ROOT / "htmlcov",
                ROOT / ".pytest_cache",
                ROOT / ".ruff_cache",
                ROOT / ".mypy_cache",
                ROOT / ".hypothesis",
            ]
        )
        paths.extend(python_cache_paths())
    if "docs" in groups:
        paths.append(ROOT / "docs" / "_build")
    if "package" in groups:
        paths.extend([ROOT / "dist", ROOT / "build"])
        paths.extend(ROOT.glob("*.egg-info"))
        paths.extend(ROOT.glob("pip-wheel-metadata"))
    if "temp" in groups:
        paths.extend(TMP_ROOT.glob("sftpwarden-*"))
    if "tox" in groups:
        paths.append(ROOT / ".tox")
    return unique_paths(paths)


def python_cache_paths() -> list[Path]:
    """Return Python cache paths outside managed environment directories."""
    paths: list[Path] = []
    for current, dirs, files in os.walk(ROOT):
        current_path = Path(current)
        relative = current_path.relative_to(ROOT)
        kept_dirs = []
        for dirname in dirs:
            if dirname == "__pycache__":
                paths.append(current_path / dirname)
                continue
            if str(relative / dirname) in WALK_EXCLUDED_DIRS or dirname in WALK_EXCLUDED_DIRS:
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in files:
            if filename.endswith((".pyc", ".pyo")):
                paths.append(current_path / filename)
    return paths


def unique_paths(paths: list[Path]) -> list[Path]:
    """Return paths once, preserving input order."""
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def remove_path(path: Path, result: CleanupResult, *, dry_run: bool) -> None:
    """Remove one safe artifact path."""
    if not path.exists() and not path.is_symlink():
        return
    resolved = path.resolve(strict=False)
    if not is_safe_path(resolved):
        result.add_skipped(f"unsafe path: {path}")
        return
    if dry_run:
        result.add_removed(str(path))
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    result.add_removed(str(path))


def is_safe_path(path: Path) -> bool:
    """Return whether a path is inside the project or SFTPWarden temp space."""
    root = ROOT.resolve()
    tmp = TMP_ROOT.resolve()
    if path in (root, tmp):
        return False
    return path.is_relative_to(root) or (
        path.is_relative_to(tmp) and path.name.startswith("sftpwarden-")
    )


def remove_docker_images(result: CleanupResult, *, dry_run: bool) -> None:
    """Remove Docker smoke images when Docker is available."""
    docker = shutil.which("docker")
    if docker is None:
        result.add_skipped("docker executable not found")
        return
    if dry_run:
        for image in DOCKER_SMOKE_IMAGES:
            result.add_removed(f"docker image {image}")
        return
    completed = subprocess.run(  # noqa: S603
        [docker, "image", "rm", "-f", *DOCKER_SMOKE_IMAGES],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        result.add_skipped(completed.stderr.strip() or "docker image cleanup failed")
        return
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    for image in DOCKER_SMOKE_IMAGES:
        if image in output or completed.returncode == 0:
            result.add_removed(f"docker image {image}")


def cleanup(groups: set[str], *, docker: bool, dry_run: bool) -> CleanupResult:
    """Clean artifacts and return a cleanup report."""
    result = CleanupResult()
    if "tox" in groups and os.environ.get("TOX_ENV_NAME"):
        groups = set(groups)
        groups.remove("tox")
        result.add_skipped(".tox cleanup skipped from inside tox")
    for path in artifact_paths(groups):
        remove_path(path, result, dry_run=dry_run)
    if docker:
        remove_docker_images(result, dry_run=dry_run)
    return result


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--group",
        action="append",
        choices=["python", "docs", "package", "temp", "tox"],
        help="Artifact group to clean. Can be passed multiple times.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Clean python, docs, package, and temp artifacts.",
    )
    parser.add_argument("--tox", action="store_true", help="Also remove the .tox directory.")
    parser.add_argument("--docker", action="store_true", help="Remove Docker smoke-test images.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be removed.")
    parser.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    return parser.parse_args()


def selected_groups(args: argparse.Namespace) -> set[str]:
    """Return cleanup groups selected from parsed arguments."""
    groups = set(args.group or [])
    if args.all or not groups:
        groups.update({"python", "docs", "package", "temp"})
    if args.tox:
        groups.add("tox")
    return groups


def main() -> int:
    """Run artifact cleanup."""
    args = parse_args()
    result = cleanup(selected_groups(args), docker=args.docker, dry_run=args.dry_run)
    if not args.quiet:
        verb = "Would remove" if args.dry_run else "Removed"
        for item in result.removed:
            print(f"{verb}: {item}")
        for item in result.skipped:
            print(f"Skipped: {item}")
        if not result.removed and not result.skipped:
            print("No artifacts to clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
