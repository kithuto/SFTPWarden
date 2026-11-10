"""GitHub Actions workflow policy tests."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def workflow_text(name: str) -> str:
    """Read a workflow file by name."""
    return Path(".github/workflows", name).read_text(encoding="utf-8")


def assert_uses_action(text: str, action: str) -> None:
    """Assert that a workflow uses an action without pinning a specific version."""
    assert re.search(rf"uses:\s+{re.escape(action)}@", text)


def workflow_data(name: str) -> dict:
    """Read workflow structure without YAML 1.1 converting the `on` key to True."""
    document = yaml.safe_load(workflow_text(name))
    # PyYAML uses YAML 1.1; normalize this one GitHub Actions schema key explicitly.
    if True in document:
        assert "on" not in document
        document["on"] = document.pop(True)
    return document


def test_ci_validates_integration_and_promotion_changes() -> None:
    workflow = workflow_data("ci.yml")
    assert set(workflow["on"]) == {"workflow_call"}
    release = workflow_data("release.yml")
    trigger = release["on"]["pull_request"]
    assert set(trigger["branches"]) == {"dev", "main"}
    assert "paths" not in trigger
    assert "pull_request_target" not in release["on"]
    assert release["permissions"] == {"contents": "read"}
    assert set(release["on"]["push"]["branches"]) == {"dev", "main"}
    assert release["jobs"]["ordinary"]["uses"] == "./.github/workflows/ci.yml"
    assert release["concurrency"]["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"


def test_ci_runs_all_supported_python_versions_on_each_native_host() -> None:
    job = workflow_data("ci.yml")["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert set(matrix["python-version"]) == {"3.11", "3.12", "3.13", "3.14"}
    assert {
        item["python-version"]: item["tox-env"] for item in matrix["include"] if "os" not in item
    } == {
        "3.11": "py311",
        "3.12": "py312",
        "3.13": "py313",
        "3.14": "py314",
    }
    assert [item for item in matrix["include"] if "os" in item] == [
        {"os": "ubuntu-latest", "python-version": "3.14", "tox-env": "coverage"}
    ]
    assert "exclude" not in matrix
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job.get("continue-on-error", False) is False
    assert job["strategy"]["fail-fast"] is False
    execution = next(step for step in job["steps"] if step.get("id") == "tests")
    assert execution["run"] == "tox -e ${{ matrix.tox-env }} -- --junitxml=test-results.xml"
    assert execution.get("continue-on-error", False) is False
    report = next(step for step in job["steps"] if "upload-artifact@" in step.get("uses", ""))
    assert report["if"] == "always()"
    assert report["with"]["path"] == "test-results.xml"
    assert "matrix.os" in report["with"]["name"]
    assert "matrix.python-version" in report["with"]["name"]


def test_ci_quality_enforces_coverage_with_existing_checks() -> None:
    steps = workflow_data("ci.yml")["jobs"]["quality"]["steps"]
    assert any("tox -e lint,mypy,docs,audit" in step.get("run", "") for step in steps)
    assert not any(
        "coverage" in step.get("run", "") or "package," in step.get("run", "") for step in steps
    )
    candidate = str(workflow_data("release.yml")["jobs"]["packages"]["steps"])
    assert "python -m twine check dist/*.whl dist/*.tar.gz" in candidate
    assert "Generated artifacts in candidate wheel" in candidate


def test_docs_workflow_only_builds_and_deploys_from_main() -> None:
    """Keep documentation publication scoped to main branch updates."""
    text = workflow_text("docs.yml")

    assert "push:" in text
    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert "branches: [dev]" not in text
    assert "README.md" in text
    assert "docs/**" in text
    assert ".github/workflows/docs.yml" in text
    assert "CHANGELOG.md" in text
    assert_uses_action(text, "actions/upload-pages-artifact")
    assert_uses_action(text, "actions/deploy-pages")


def test_security_scans_contributors_without_write_credentials() -> None:
    workflow = workflow_data("security.yml")
    assert "pull_request" not in workflow["on"]
    assert "workflow_call" in workflow["on"]
    assert {"schedule", "workflow_dispatch"} <= workflow["on"].keys()
    assert "pull_request_target" not in workflow["on"]
    scan = workflow["jobs"]["container-scan"]
    assert scan["permissions"] == {"contents": "read", "actions": "read"}
    scans = [step for step in scan["steps"] if "trivy-action@" in step.get("uses", "")]
    assert len(scans) == 2
    # Scan once without discarding lower-severity findings. The separate gate
    # must not inherit trivy-action's SARIF severity override.
    assert all(step["with"]["format"] == "json" for step in scans)
    assert all(step["with"]["exit-code"] == "0" for step in scans)
    assert all(step["with"]["severity"] == "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL" for step in scans)
    assert all(step["with"]["ignore-unfixed"] is False for step in scans)
    assert all(step.get("continue-on-error", False) is False for step in scans)
    gate = next(step for step in scan["steps"] if step.get("id") == "container-policy")
    assert gate["if"] == "always()"
    assert gate.get("continue-on-error", False) is False
    assert "for image in runtime watcher" in gate["run"]
    assert "--format sarif --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL" in gate["run"]
    assert "--format table --severity HIGH,CRITICAL --exit-code 1" in gate["run"]
    assert 'exit "$status"' in gate["run"]
    upload = next(step for step in scan["steps"] if "upload-artifact@" in step.get("uses", ""))
    assert upload["if"] == "always()"
    for image in ("runtime", "watcher"):
        assert f"trivy-{image}.json" in upload["with"]["path"]
        assert f"trivy-{image}.sarif" in upload["with"]["path"]
    publisher = workflow["jobs"]["publish-sarif"]
    assert "head.repo.full_name == github.repository" in publisher["if"]
    assert not any(
        "run" in step or "checkout@" in step.get("uses", "") for step in publisher["steps"]
    )


def test_docs_and_promotion_routes_do_not_repeat_the_full_candidate_graph() -> None:
    jobs = workflow_data("release.yml")["jobs"]
    for name in ("ordinary", "packages"):
        assert jobs[name]["if"] == "needs.version.outputs.mode == 'full'"
        assert "version" in jobs[name]["needs"]
    assert jobs["documentation"]["if"] == "needs.version.outputs.mode == 'docs'"
    docs_commands = str(jobs["documentation"]["steps"])
    assert "sphinx" in docs_commands
    assert all(command not in docs_commands for command in ("pytest", "docker", "kind", "tox"))
    assert jobs["reuse"]["if"] == "needs.version.outputs.mode == 'reuse'"
    assert jobs["reuse"]["uses"] == "./.github/workflows/reuse.yml"
    assert jobs["security"]["needs"] == ["version", "images", "reuse"]
    assert jobs["security"]["uses"] == "./.github/workflows/security.yml"
    assert (
        jobs["security"]["with"]["candidate-commit"]
        == "${{ needs.version.outputs.candidate_commit }}"
    )
    assert jobs["version"]["permissions"] == {"contents": "read", "actions": "read"}
    assert "ci_candidate.py plan" in str(jobs["version"]["steps"])
    assert "candidate-validation" in str(jobs["gate"]["steps"])
    for name in (
        "publication-preflight",
        "publish-python",
        "publish-images",
        "publish-chart",
        "published-smoke",
        "complete-release",
    ):
        assert (
            jobs[name]["env"]["CANDIDATE_COMMIT"] == "${{ needs.version.outputs.candidate_commit }}"
        )
        assert 'verify dist --commit "$GITHUB_SHA"' not in str(jobs[name])
    assert "sftpwarden-candidate:$CANDIDATE_COMMIT-$ARCH" in str(jobs["publish-images"])
    assert '"$GITHUB_SHA"' in str(jobs["complete-release"])


def test_reused_artifacts_keep_original_provenance_and_are_rescanned_once() -> None:
    job = workflow_data("reuse.yml")["jobs"]["artifacts"]
    assert set(job["strategy"]["matrix"]["artifact"]) == {
        "candidate-packages",
        "candidate-images-amd64",
        "candidate-images-arm64",
    }
    downloads = [s for s in job["steps"] if "download-artifact@" in s.get("uses", "")]
    assert all(s["with"]["run-id"] == "${{ inputs.source-run-id }}" for s in downloads)
    commands = str(job["steps"])
    assert "ci_candidate.py verify" in commands
    assert 'release_artifacts.py verify candidate --commit "$CANDIDATE_COMMIT"' in commands
    assert "docker build" not in commands and "python -m build" not in commands
    scans = workflow_data("security.yml")["jobs"]["container-scan"]
    assert {m["arch"] for m in scans["strategy"]["matrix"]["include"]} == {"amd64", "arm64"}
    builds = [s for s in scans["steps"] if "docker build" in s.get("run", "")]
    assert len(builds) == 2 and all(s["if"] == "inputs.candidate-commit == ''" for s in builds)
    assert "docker load" in str(scans["steps"])
    assert "trivy-action@" not in workflow_text("docker.yml")
    assert set(workflow_data("scorecard.yml")["on"]) == {"schedule", "workflow_dispatch"}


def test_candidate_images_are_built_once_and_tested_on_native_platforms() -> None:
    workflow = workflow_data("docker.yml")
    assert set(workflow["on"]) == {"workflow_call"}
    job = workflow["jobs"]["images"]
    matrix = job["strategy"]["matrix"]["include"]
    assert {item["arch"]: item["runner"] for item in matrix} == {
        "amd64": "ubuntu-24.04",
        "arm64": "ubuntu-24.04-arm",
    }
    assert workflow["permissions"] == {"contents": "read"}
    text = workflow_text("docker.yml")
    assert text.count("docker build --target") == 2
    assert "runtime-wheel" in text and "watcher-wheel" in text
    assert "SFTPWARDEN_TEST_INSTALLED_ARTIFACT" in text
    assert "python -I tools/run_release_gates.py load" in text
    assert "not release_kubernetes" not in text
    assert "docker save" in text and "docker push" not in text
    assert "runtime-sbom.spdx.json" in text and "watcher-sbom.spdx.json" in text


def test_publication_depends_on_complete_candidate_graph_and_never_rebuilds() -> None:
    jobs = workflow_data("release.yml")["jobs"]
    tools = next(
        step
        for step in jobs["cluster"]["steps"]
        if step.get("name") == "Install pinned disposable cluster tools"
    )
    assert "go install" not in tools["run"]
    assert 'echo "$KIND_SHA256  kind" | sha256sum --check' in tools["run"]
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", item["kind_sha256"])
        for item in jobs["cluster"]["strategy"]["matrix"]["include"]
    )
    assert "KUBECONFIG" not in jobs["cluster"]["env"]
    assert {
        (item["arch"], item["group"]) for item in jobs["cluster"]["strategy"]["matrix"]["include"]
    } == {("amd64", "current"), ("arm64", "current"), ("amd64", "upgrade")}
    assert any(
        'echo "KUBECONFIG=$RUNNER_TEMP/candidate-kubeconfig" >> "$GITHUB_ENV"'
        in step.get("run", "")
        for step in jobs["cluster"]["steps"]
    )
    sdist = next(
        step for step in jobs["packages"]["steps"] if "candidate sdist" in step.get("name", "")
    )
    assert sdist["env"]["SFTPWARDEN_TEST_INSTALLED_ARTIFACT"] == "1"
    assert "dist/sftpwarden-$VERSION.tar.gz[mysql,postgres,mongodb]" in sdist["run"]
    assert "-I -m pytest tests/release_validation/test_local_user_workflows.py" in sdist["run"]
    assert set(jobs["gate"]["needs"]) == {
        "version",
        "ordinary",
        "packages",
        "images",
        "contracts",
        "external",
        "cluster",
        "documentation",
        "reuse",
        "security",
    }
    assert jobs["gate"]["if"] == "always()"
    assert "ci_candidate.py gate" in str(jobs["gate"]["steps"])
    preflight = jobs["publication-preflight"]
    assert set(preflight["needs"]) == {"version", "gate"}
    assert "needs.version.outputs.publish == 'true'" in preflight["if"]
    for name in ("publish-python", "publish-chart", "publish-images"):
        job = jobs[name]
        assert "publication-preflight" in job["needs"]
        assert "always()" not in job.get("if", "")
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        assert "release_artifacts.py verify" in commands
        assert not any(
            command in commands for command in ("docker build", "helm package", "python -m build")
        )
    assert set(jobs["published-smoke"]["needs"]) >= {
        "publish-python",
        "publish-chart",
        "publish-indexes",
    }
    assert "published-smoke" in jobs["complete-release"]["needs"]
    assert "--latest" not in str(jobs["publish-indexes"])
    assert "--latest" in str(jobs["complete-release"])
    assert jobs["publish-python"]["permissions"] == {"contents": "read", "id-token": "write"}


def test_publication_chain_handles_skipped_ancestors_without_ignoring_failed_gates() -> None:
    """Main reuse skips build jobs; implicit success() must not skip publication."""
    jobs = workflow_data("release.yml")["jobs"]
    for name in (
        "publication-preflight",
        "publish-python",
        "publish-images",
        "publish-chart",
        "publish-indexes",
        "published-smoke",
        "complete-release",
    ):
        condition = jobs[name].get("if", "")
        assert "!cancelled()" in condition, name
        assert "needs.version.outputs.publish == 'true'" in condition, name
        for dependency in jobs[name]["needs"]:
            assert f"needs.{dependency}.result == 'success'" in condition, (name, dependency)


def test_publication_has_an_explicit_recovery_request_and_completion_gate() -> None:
    workflow = workflow_data("release.yml")
    option = workflow["on"]["workflow_dispatch"]["inputs"]["publish"]
    assert option["type"] == "boolean" and option["default"] is False
    jobs = workflow["jobs"]
    assert jobs["version"]["outputs"]["publish"] == "${{ steps.version.outputs.publish }}"
    gate = jobs["publication-gate"]
    assert set(gate["needs"]) == {
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
    assert gate["if"] == "always() && needs.version.outputs.publish == 'true'"
    assert "ci_candidate.py publication-gate" in str(gate["steps"])


def test_external_groups_reuse_verified_images_and_do_not_serialize_clusters() -> None:
    jobs = workflow_data("release.yml")["jobs"]
    assert jobs["packages"]["needs"] == ["version"]
    assert "external" not in jobs["cluster"]["needs"]
    assert set(jobs["external"]["needs"]) == {"packages", "images"}
    assert "run_release_gates.py contracts" in str(jobs["contracts"])
    external = workflow_data("external.yml")["jobs"]["external"]
    assert set(external["strategy"]["matrix"]["arch"]) == {"amd64", "arm64"}
    assert set(external["strategy"]["matrix"]["group"]) == {
        "policy",
        "deployment",
        "delivery",
        "databases",
        "recovery",
    }
    commands = "\n".join(step.get("run", "") for step in external["steps"])
    assert "docker build" not in commands
    assert "release_artifacts.py verify candidate-images" in commands
    assert "docker load" in commands
    assert 'run_release_gates.py "$GROUP"' in commands


def test_external_actions_are_immutable_and_failure_gates_are_not_waived() -> None:
    for path in Path(".github/workflows").glob("*.yml"):
        workflow = workflow_data(path.name)
        for job in workflow["jobs"].values():
            assert not job.get("continue-on-error", False)
            for step in job.get("steps", []):
                assert not step.get("continue-on-error", False)
                action = step.get("uses", "")
                if action and not action.startswith("./"):
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (path.name, action)
