from __future__ import annotations

from pathlib import Path


def workflow_text(name: str) -> str:
    return Path(".github/workflows", name).read_text(encoding="utf-8")


def test_ci_runs_for_dev_code_changes_not_docs_only() -> None:
    text = workflow_text("ci.yml")

    assert "branches: [dev]" in text
    assert "sftpwarden/**" in text
    assert "tests/**" in text
    assert "pyproject.toml" in text
    assert "README.md" not in text
    assert "docs/**" not in text
    assert "tox-env: py311" in text
    assert "tox-env: py312" in text
    assert "tox-env: py313" in text


def test_docs_workflow_only_builds_and_deploys_from_main() -> None:
    text = workflow_text("docs.yml")

    assert "push:" in text
    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert "branches: [dev]" not in text
    assert "README.md" in text
    assert "docs/**" in text
    assert "actions/upload-pages-artifact@v5.0.0" in text
    assert "actions/deploy-pages@v5.0.0" in text


def test_security_workflow_is_main_or_manual_only() -> None:
    text = workflow_text("security.yml")

    assert "branches: [main]" in text
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert "branches: [dev]" not in text
    assert "sftpwarden-watcher:security" in text
    assert "watcher-sbom.spdx.json" in text
    assert "github/codeql-action/upload-sarif@v4.36.2" in text
    assert "github/codeql-action/upload-sarif@v3" not in text


def test_docker_workflow_publishes_from_main_only_when_version_changes() -> None:
    text = workflow_text("docker.yml")

    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert 'tags: ["v*"]' not in text
    assert "BEFORE_SHA: ${{ github.event.before }}" in text
    assert "previous_version" in text
    assert "changed: ${{ steps.version.outputs.changed }}" in text
    assert "if: needs.version.outputs.changed == 'true'" in text
    assert "docker/login-action@v4.2.0" in text
    assert "docker/build-push-action@v7.2.0" in text


def test_release_workflow_publishes_from_main_only_when_version_changes() -> None:
    text = workflow_text("release.yml")

    assert "branches: [main]" in text
    assert 'tags: ["v*"]' not in text
    assert "BEFORE_SHA: ${{ github.event.before }}" in text
    assert "previous_version" in text
    assert "changed: ${{ steps.version.outputs.changed }}" in text
    assert "if: needs.verify.outputs.changed == 'true'" in text
    assert "pyproject.toml" in text
    assert "sftpwarden.__version__" in text
    assert "pypa/gh-action-pypi-publish" in text
    assert "softprops/action-gh-release" in text
