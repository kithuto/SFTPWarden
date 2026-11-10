"""Promotion reuse must preserve validation, provenance and docs-only routing."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def routing():
    spec = importlib.util.spec_from_file_location("ci_candidate", Path("tools/ci_candidate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate():
    return {
        "id": 40,
        "event": "push",
        "head_branch": "dev",
        "head_sha": "a" * 40,
        "path": ".github/workflows/release.yml",
        "head_repository": {"full_name": "test/repo"},
        "head_commit": {"tree_id": "b" * 40},
        "status": "completed",
        "conclusion": "success",
    }


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["docs/guide.md"],
        ["README.md", "docs/conf.py"],
        ["CHANGELOG.md"],
    ],
)
def test_documentation_paths_need_only_the_docs_route(routing, paths):
    assert routing.docs_only(paths)


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "sftpwarden/cli.py",
        "docker/runtime/Dockerfile",
        "charts/sftpwarden/Chart.yaml",
        "tests/test_docs.py",
        "LICENSE",
        ".github/workflows/release.yml",
        ".github/workflows/docs.yml",
        "tools/ci_candidate.py",
        "new-unknown-file",
        "docs-other/script.py",
    ],
)
def test_unknown_or_executable_changes_cannot_skip_full_validation(routing, path):
    assert not routing.docs_only(["README.md", path])


def test_git_diff_handles_renames_deletions_and_large_change_sets(routing, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci@example.invalid")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "sftpwarden").mkdir()
    (tmp_path / "sftpwarden/code.py").write_text("before\n")
    git("add", ".")
    git("commit", "-qm", "initial")
    before = git("rev-parse", "HEAD")
    (tmp_path / "docs").mkdir()
    (tmp_path / "sftpwarden/code.py").rename(tmp_path / "docs/moved.md")
    for index in range(350):
        (tmp_path / f"docs/page-{index}.md").write_text("docs\n")
    git("add", ".")
    git("commit", "-qm", "renamed code and docs")
    paths = routing.changes(before, "HEAD")
    assert len(paths) == 352
    assert "sftpwarden/code.py" in paths and "docs/moved.md" in paths
    assert not routing.docs_only(paths)
    assert routing.changes("0" * 40, "HEAD") is None
    assert routing.changes("unknown", "HEAD") is None
    assert routing.changes("f" * 40, "HEAD") is None
    assert routing.changes(git("rev-parse", "HEAD"), "HEAD") == []


def install_api(monkeypatch, routing, runs, *, expired=None, missing=None):
    def request(repository, path):
        assert repository == "test/repo"
        if "/artifacts?" in path:
            return {
                "artifacts": [
                    {"name": name, "expired": name == expired}
                    for name in routing.ARTIFACTS
                    if name != missing
                ]
            }
        return {"workflow_runs": runs}

    monkeypatch.setattr(routing, "request", request)


def test_only_complete_identical_dev_candidate_can_be_reused(routing, candidate, monkeypatch):
    install_api(monkeypatch, routing, [candidate])
    assert routing.reusable_run("test/repo", "b" * 40, 99) == candidate
    assert routing.reusable_run("test/repo", "c" * 40, 99) is None
    assert routing.reusable_run("test/repo", "b" * 40, 40) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("head_branch", "main"),
        ("path", ".github/workflows/other.yml"),
        ("head_repository", {"full_name": "untrusted/fork"}),
    ],
)
def test_other_workflows_branches_and_forks_are_not_trusted(
    routing, candidate, monkeypatch, field, value
):
    candidate[field] = value
    install_api(monkeypatch, routing, [candidate])
    assert routing.reusable_run("test/repo", "b" * 40, 99) is None


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped"])
def test_failed_candidate_cannot_fall_back_to_an_older_success(
    routing, candidate, monkeypatch, conclusion
):
    failed = {**candidate, "id": 41, "conclusion": conclusion}
    install_api(monkeypatch, routing, [failed, candidate])
    with pytest.raises(RuntimeError, match="failed or was cancelled"):
        routing.reusable_run("test/repo", "b" * 40, 99)


@pytest.mark.parametrize(
    "artifact",
    [
        "candidate-packages",
        "candidate-images-amd64",
        "candidate-images-arm64",
        "candidate-validation",
    ],
)
@pytest.mark.parametrize("kind", ["expired", "missing"])
def test_incomplete_or_expired_artifacts_require_fresh_validation(
    routing, candidate, monkeypatch, artifact, kind
):
    install_api(monkeypatch, routing, [candidate], **{kind: artifact})
    assert routing.reusable_run("test/repo", "b" * 40, 99) is None


def test_waits_for_same_candidate_and_does_not_start_duplicate_validation(
    routing, candidate, monkeypatch
):
    pending = {**candidate, "status": "in_progress", "conclusion": None}
    responses = iter([None, pending, candidate])
    monkeypatch.setattr(routing, "reusable_run", lambda *args: next(responses))
    monkeypatch.setattr(routing.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(routing.time, "monotonic", lambda: 0)
    assert routing.select_candidate("test/repo", "b" * 40, 99, discovery_seconds=90) == candidate


def test_pending_validation_times_out_without_pretending_success(routing, candidate, monkeypatch):
    pending = {**candidate, "status": "in_progress"}
    monkeypatch.setattr(routing, "reusable_run", lambda *args: pending)
    clock = iter([0, 2200])
    monkeypatch.setattr(routing.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="still running"):
        routing.select_candidate("test/repo", "b" * 40, 99)


@pytest.mark.parametrize(
    "mode,required",
    [
        (
            "full",
            {
                "ordinary",
                "packages",
                "images",
                "contracts",
                "external",
                "cluster",
                "security",
                "version",
            },
        ),
        ("reuse", {"reuse", "security", "version"}),
        ("docs", {"documentation", "version"}),
    ],
)
def test_aggregate_requires_every_job_for_the_selected_route(routing, mode, required):
    results = {name: {"result": "success"} for name in required}
    routing.require_results(mode, results)
    for name in required:
        for outcome in ("failure", "skipped", "cancelled"):
            with pytest.raises(RuntimeError):
                routing.require_results(mode, {**results, name: {"result": outcome}})
        missing = results.copy()
        missing.pop(name)
        with pytest.raises(RuntimeError):
            routing.require_results(mode, missing)
    routing.require_results(mode, {**results, "unused": {"result": "skipped"}})
    with pytest.raises(RuntimeError):
        routing.require_results(mode, {**results, "unused": {"result": "failure"}})
    with pytest.raises(ValueError):
        routing.require_results("unexpected", results)


def test_downloaded_receipt_matches_the_selected_run(routing, tmp_path):
    path = tmp_path / "proof.json"
    data = {"format": 1, "commit": "a" * 40, "tree": "b" * 40, "run_id": "40"}
    path.write_text(json.dumps(data))
    routing.verify_receipt(path, "a" * 40, "b" * 40, "40")
    for key in data:
        path.write_text(json.dumps({**data, key: "wrong"}))
        with pytest.raises(ValueError):
            routing.verify_receipt(path, "a" * 40, "b" * 40, "40")


@pytest.mark.parametrize(
    "event_name,ref,head_repo,base_branch,head_branch,paths,expected",
    [
        ("push", "refs/heads/dev", None, None, None, ["sftpwarden/cli.py"], "full"),
        ("push", "refs/heads/main", None, None, None, ["sftpwarden/cli.py"], "reuse"),
        ("push", "refs/heads/dev", None, None, None, ["README.md"], "docs"),
        (
            "pull_request",
            "refs/pull/1/merge",
            "test/repo",
            "main",
            "dev",
            ["sftpwarden/cli.py"],
            "reuse",
        ),
        (
            "pull_request",
            "refs/pull/1/merge",
            "fork/repo",
            "main",
            "dev",
            ["sftpwarden/cli.py"],
            "full",
        ),
        (
            "pull_request",
            "refs/pull/1/merge",
            "test/repo",
            "dev",
            "fix",
            ["sftpwarden/cli.py"],
            "full",
        ),
        (
            "pull_request",
            "refs/pull/1/merge",
            "test/repo",
            "main",
            "fix",
            ["sftpwarden/cli.py"],
            "full",
        ),
        ("pull_request", "refs/pull/1/merge", "test/repo", "main", "dev", ["README.md"], "docs"),
        ("workflow_dispatch", "refs/heads/dev", None, None, None, ["README.md"], "full"),
        ("push", "refs/heads/dev", None, None, None, None, "full"),
    ],
)
def test_event_routing_and_original_candidate_identity(
    routing,
    candidate,
    monkeypatch,
    tmp_path,
    event_name,
    ref,
    head_repo,
    base_branch,
    head_branch,
    paths,
    expected,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.4.0"\n')
    monkeypatch.setattr(routing, "changes", lambda *args: paths)
    monkeypatch.setattr(routing, "git", lambda *args: "b" * 40)
    monkeypatch.setattr(
        routing.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, '[project]\nversion = "1.3.0"\n', ""
        ),
    )
    looked_up = []

    def select(*args, **kwargs):
        looked_up.append(args)
        return candidate

    monkeypatch.setattr(routing, "select_candidate", select)
    event = {"before": "c" * 40}
    if head_repo:
        event["pull_request"] = {
            "base": {"sha": "c" * 40, "ref": base_branch},
            "head": {"ref": head_branch, "repo": {"full_name": head_repo}},
        }
    result = routing.plan(event, event_name, ref, "d" * 40, 99, "test/repo")
    assert result["mode"] == expected
    assert bool(looked_up) == (expected == "reuse")
    assert result["candidate_commit"] == ("a" * 40 if expected == "reuse" else "d" * 40)
    assert result["source_run_id"] == ("40" if expected == "reuse" else "99")
    assert result["changed"] == str(event_name == "push" and paths is not None).lower()


def test_api_errors_cannot_be_treated_as_valid_evidence(routing, monkeypatch):
    def failure(*args):
        raise OSError("metadata unavailable")

    monkeypatch.setattr(routing, "request", failure)
    with pytest.raises(OSError, match="metadata unavailable"):
        routing.select_candidate("test/repo", "b" * 40, 99)


def test_workflow_entry_points_with_real_git_and_environment(tmp_path):
    script = Path("tools/ci_candidate.py").resolve()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci@example.invalid")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.4.0"\n')
    git("add", ".")
    git("commit", "-qm", "initial")
    base = git("rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("Documentation update\n")
    git("add", ".")
    git("commit", "-qm", "documentation")
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": base}))
    output = tmp_path / "outputs"
    env = {
        **os.environ,
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/dev",
        "GITHUB_SHA": commit,
        "GITHUB_RUN_ID": "99",
        "GITHUB_REPOSITORY": "test/repo",
        "CI_TREE": tree,
    }

    def run(action):
        return subprocess.run(
            [sys.executable, str(script), action],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    result = run("plan")
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values == {
        "mode": "docs",
        "version": "1.4.0",
        "changed": "false",
        "tree": tree,
        "candidate_commit": commit,
        "source_run_id": "99",
    }
    env.update(
        CI_MODE="docs",
        RESULTS=json.dumps({n: {"result": "success"} for n in ("version", "documentation")}),
    )
    assert run("gate").returncode == 0
    assert not (tmp_path / "candidate-validation.json").exists()
    env["CI_MODE"] = "full"
    assert run("gate").returncode != 0
    assert not (tmp_path / "candidate-validation.json").exists()
    env["RESULTS"] = json.dumps(
        {
            n: {"result": "success"}
            for n in (
                "version",
                "ordinary",
                "packages",
                "images",
                "contracts",
                "external",
                "cluster",
                "security",
            )
        }
    )
    assert run("gate").returncode == 0
    proof = tmp_path / "candidate-proof"
    proof.mkdir()
    (tmp_path / "candidate-validation.json").rename(proof / "candidate-validation.json")
    env.update(CANDIDATE_COMMIT=commit, SOURCE_RUN_ID="99")
    assert run("verify").returncode == 0
    env["SOURCE_RUN_ID"] = "100"
    assert run("verify").returncode != 0


def test_candidate_search_continues_to_the_next_page(routing, candidate, monkeypatch):
    calls = []

    def request(repository, path):
        calls.append(path)
        if path.endswith("&page=1"):
            return {"workflow_runs": [{**candidate, "head_commit": {"tree_id": "c" * 40}}] * 100}
        if path.endswith("&page=2"):
            return {"workflow_runs": [candidate]}
        return {"artifacts": [{"name": name, "expired": False} for name in routing.ARTIFACTS]}

    monkeypatch.setattr(routing, "request", request)
    assert routing.reusable_run("test/repo", "b" * 40, 99) == candidate
    assert len(calls) == 3
