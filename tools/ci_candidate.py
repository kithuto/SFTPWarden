"""Route CI conservatively and reuse only complete, identical trusted candidates."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import tomllib
import urllib.request
from pathlib import Path

DOC_FILES = frozenset(
    {
        "README.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "AGENTS.md",
    }
)
ARTIFACTS = frozenset(
    {
        "candidate-packages",
        "candidate-images-amd64",
        "candidate-images-arm64",
        "candidate-validation",
    }
)
FULL_JOBS = frozenset({"ordinary", "packages", "images", "contracts", "external", "cluster"})
PUBLICATION_JOBS = frozenset(
    {
        "version",
        "gate",
        "publication-preflight",
        "publish-python",
        "publish-images",
        "publish-chart",
        "publish-indexes",
        "published-smoke",
        "complete-release",
    }
)


def publication_requested(event: dict, event_name: str, ref: str, changed: bool) -> bool:
    """Publish version bumps on main or explicitly requested recovery on main."""
    value = event.get("inputs", {}).get("publish", False)
    manual = event_name == "workflow_dispatch" and (value is True or value == "true")
    if manual and ref != "refs/heads/main":
        raise ValueError("Publication recovery is only allowed on main.")
    return ref == "refs/heads/main" and ((event_name == "push" and changed) or manual)


def git(*args: str) -> str:
    """Read exact Git state without evaluating shell input."""
    return subprocess.check_output(["git", *args], text=True).strip()


def docs_only(paths: list[str]) -> bool:
    """Unknown paths require full validation; renames must include both names."""
    return all(path in DOC_FILES or path.startswith("docs/") for path in paths)


def changes(base: str, head: str) -> list[str] | None:
    """Use the complete diff, including removals, without GitHub's path limits."""
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        return None
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", base, head],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.stdout.rstrip("\0").split("\0")
        if result.returncode == 0 and result.stdout
        else ([] if result.returncode == 0 else None)
    )


def request(repository: str, path: str) -> dict:
    """Read Actions metadata with a scoped token and fail on ambiguous responses."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("An explicit GitHub owner/repository is required.")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    message = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/{path}", headers=headers
    )
    with urllib.request.urlopen(message, timeout=30) as response:  # noqa: S310
        return json.load(response)


def reusable_run(repository: str, tree: str, current_run: int) -> dict | None:
    """Trust complete dev push/manual validations, never fork/PR artifacts."""
    for page in range(1, 6):
        runs = request(
            repository,
            f"actions/workflows/release.yml/runs?branch=dev&per_page=100&page={page}",
        )["workflow_runs"]
        for run in runs:
            if (
                run["id"] == current_run
                or run.get("event") not in {"push", "workflow_dispatch"}
                or run.get("head_branch") != "dev"
                or run.get("path") != ".github/workflows/release.yml"
                or run.get("head_repository", {}).get("full_name", "").lower() != repository.lower()
                or run.get("head_commit", {}).get("tree_id") != tree
            ):
                continue
            if run["status"] != "completed":
                return run
            if run["conclusion"] != "success":
                raise RuntimeError(
                    "The matching dev candidate failed or was cancelled; inspect it."
                )
            artifacts = request(repository, f"actions/runs/{run['id']}/artifacts?per_page=100")[
                "artifacts"
            ]
            available = {a["name"] for a in artifacts if not a["expired"]}
            # A docs-only or old workflow run is not proof of full validation.
            if available >= ARTIFACTS:
                return run
            return None
        if len(runs) < 100:
            break
    return None


def select_candidate(
    repository: str,
    tree: str,
    current_run: int,
    *,
    discovery_seconds: int = 0,
    wait_seconds: int = 2100,
) -> dict | None:
    """Wait for an identical in-flight dev candidate instead of testing it twice."""
    start = time.monotonic()
    waiting_for = None
    while True:
        run = reusable_run(repository, tree, current_run)
        if run is None:
            if time.monotonic() - start >= discovery_seconds:
                return None
        elif run["status"] == "completed":
            return run
        elif time.monotonic() - start >= wait_seconds:
            raise RuntimeError(
                "The identical dev candidate is still running; retry promotion later."
            )
        if run and run["id"] != waiting_for:
            waiting_for = run["id"]
            print(f"Waiting for identical dev candidate run {waiting_for}.", flush=True)
        time.sleep(15)


def plan(event: dict, event_name: str, ref: str, commit: str, run_id: int, repository: str) -> dict:
    """Choose docs, full validation, or promotion of byte-identical artifacts."""
    pr = event.get("pull_request", {})
    base = pr.get("base", {}).get("sha", "") if pr else event.get("before", "")
    paths = changes(base, commit)
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    previous = None
    if event_name == "push" and paths is not None:
        result = subprocess.run(
            ["git", "show", f"{base}:pyproject.toml"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            previous = tomllib.loads(result.stdout)["project"]["version"]
    changed = previous is not None and previous != version
    publish = publication_requested(event, event_name, ref, changed)
    result = {
        "mode": "full",
        "version": version,
        "changed": str(changed).lower(),
        "publish": str(publish).lower(),
        "tree": git("rev-parse", f"{commit}^{{tree}}"),
        "candidate_commit": commit,
        "source_run_id": str(run_id),
    }
    if event_name != "workflow_dispatch" and paths is not None and docs_only(paths):
        result["mode"] = "docs"
        return result
    promotion_pr = (
        event_name == "pull_request"
        and pr.get("base", {}).get("ref") == "main"
        and pr.get("head", {}).get("ref") == "dev"
        and pr.get("head", {}).get("repo", {}).get("full_name", "").lower() == repository.lower()
    )
    if promotion_pr or (event_name == "push" and ref == "refs/heads/main") or publish:
        source = select_candidate(
            repository, result["tree"], run_id, discovery_seconds=90 if promotion_pr else 0
        )
        if source:
            result.update(
                mode="reuse", candidate_commit=source["head_sha"], source_run_id=str(source["id"])
            )
    return result


def require_results(mode: str, results: dict) -> None:
    """Reject failed/missing required jobs and unexpected skips for each route."""
    required = {
        "full": FULL_JOBS | {"security", "version"},
        "reuse": {"reuse", "security", "version"},
        "docs": {"documentation", "version"},
    }.get(mode)
    if required is None:
        raise ValueError("Unknown CI route.")
    failures = [
        name for name in sorted(required) if results.get(name, {}).get("result") != "success"
    ]
    failures.extend(
        name
        for name, item in results.items()
        if name not in required and item.get("result") not in {"success", "skipped"}
    )
    if failures:
        raise RuntimeError("Release is blocked by: " + ", ".join(failures))


def require_publication(results: dict) -> None:
    """Reject a requested release if any publication job failed or was skipped."""
    failures = [
        name
        for name in sorted(PUBLICATION_JOBS)
        if results.get(name, {}).get("result") != "success"
    ]
    if failures:
        raise RuntimeError("Publication is incomplete: " + ", ".join(failures))


def verify_receipt(path: Path, commit: str, tree: str, run_id: str) -> None:
    """Match the downloaded full-validation receipt to the selected source run."""
    if path.is_symlink() or json.loads(path.read_text()) != {
        "format": 1,
        "commit": commit,
        "tree": tree,
        "run_id": run_id,
    }:
        raise ValueError("Candidate validation receipt does not match the selected run.")


def main() -> None:
    """Write workflow outputs or enforce the required route's aggregate check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "gate", "verify", "publication-gate"])
    args = parser.parse_args()
    if args.action == "publication-gate":
        require_publication(json.loads(os.environ["RESULTS"]))
        return
    if args.action == "verify":
        verify_receipt(
            Path("candidate-proof/candidate-validation.json"),
            os.environ["CANDIDATE_COMMIT"],
            os.environ["CI_TREE"],
            os.environ["SOURCE_RUN_ID"],
        )
        return
    if args.action == "gate":
        require_results(os.environ["CI_MODE"], json.loads(os.environ["RESULTS"]))
        if os.environ["CI_MODE"] == "full":
            Path("candidate-validation.json").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "commit": os.environ["GITHUB_SHA"],
                        "tree": os.environ["CI_TREE"],
                        "run_id": os.environ["GITHUB_RUN_ID"],
                    }
                )
                + "\n"
            )
        return
    result = plan(
        json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
        os.environ["GITHUB_EVENT_NAME"],
        os.environ["GITHUB_REF"],
        os.environ["GITHUB_SHA"],
        int(os.environ["GITHUB_RUN_ID"]),
        os.environ["GITHUB_REPOSITORY"],
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
