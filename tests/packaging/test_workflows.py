"""GitHub Actions workflow policy tests."""

from __future__ import annotations

import re
from pathlib import Path


def workflow_text(name: str) -> str:
    """Read a workflow file by name."""
    return Path(".github/workflows", name).read_text(encoding="utf-8")


def assert_uses_action(text: str, action: str) -> None:
    """Assert that a workflow uses an action without pinning a specific version."""
    assert re.search(rf"uses:\s+{re.escape(action)}@", text)


def test_ci_runs_for_dev_code_changes_not_docs_only() -> None:
    """Run CI from dev for code and chart changes, not docs-only edits."""
    text = workflow_text("ci.yml")

    assert "branches: [dev]" in text
    assert "sftpwarden/**" in text
    assert "tests/**" in text
    assert "charts/**" in text
    assert "pyproject.toml" in text
    assert "README.md" not in text
    assert "docs/**" not in text
    assert "tox-env: py311" in text
    assert "tox-env: py312" in text
    assert "tox-env: py313" in text


def test_docs_workflow_only_builds_and_deploys_from_main() -> None:
    """Keep documentation publication scoped to main branch updates."""
    text = workflow_text("docs.yml")

    assert "push:" in text
    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert "branches: [dev]" not in text
    assert "README.md" in text
    assert "docs/**" in text
    assert ".github/workflows/docs.yml" not in text
    assert_uses_action(text, "actions/upload-pages-artifact")
    assert_uses_action(text, "actions/deploy-pages")


def test_security_workflow_is_main_or_manual_only() -> None:
    """Limit scheduled security scans to main or explicit manual runs."""
    text = workflow_text("security.yml")

    assert "branches: [main]" in text
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert "branches: [dev]" not in text
    assert "sftpwarden-watcher:security" in text
    assert "watcher-sbom.spdx.json" in text
    assert_uses_action(text, "github/codeql-action/upload-sarif")


def test_docker_workflow_publishes_from_main_only_when_version_changes() -> None:
    """Publish Docker images from main only when the package version changes."""
    text = workflow_text("docker.yml")

    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert 'tags: ["v*"]' not in text
    assert "charts/**" in text
    assert ".github/workflows/docker.yml" not in text
    assert "tools/**" not in text
    assert "BEFORE_SHA: ${{ github.event.before }}" in text
    assert "previous_version" in text
    assert "changed: ${{ steps.version.outputs.changed }}" in text
    assert "if: needs.version.outputs.changed == 'true'" in text
    assert_uses_action(text, "docker/login-action")
    assert_uses_action(text, "docker/build-push-action")


def test_docker_workflow_publishes_helm_chart_after_images() -> None:
    """Publish the Helm OCI chart only after Docker images are published."""
    text = workflow_text("docker.yml")

    assert "publish-helm-chart:" in text
    assert "needs: [version, build]" in text
    assert_uses_action(text, "azure/setup-helm")
    assert 'python tools/verify_helm_release_metadata.py --version "$VERSION"' in text
    assert "helm registry login ghcr.io" in text
    package_command = (
        'helm package charts/sftpwarden --destination dist --version "$VERSION" '
        '--app-version "$VERSION"'
    )
    assert package_command in text
    assert 'helm push "dist/sftpwarden-${VERSION}.tgz" "oci://ghcr.io/${owner}/charts"' in text


def test_release_workflow_publishes_from_main_only_when_version_changes() -> None:
    """Publish releases from main only when release metadata changes."""
    text = workflow_text("release.yml")

    assert "branches: [main]" in text
    assert 'tags: ["v*"]' not in text
    assert "BEFORE_SHA: ${{ github.event.before }}" in text
    assert "previous_version" in text
    assert "changed: ${{ steps.version.outputs.changed }}" in text
    assert "if: needs.verify.outputs.changed == 'true'" in text
    assert "pyproject.toml" in text
    assert ".github/workflows/release.yml" not in text
    assert "sftpwarden.__version__" not in text
    assert "pypa/gh-action-pypi-publish" in text
    assert "softprops/action-gh-release" in text
    assert "charts/**" not in text
    assert "helm package charts/sftpwarden" not in text
    assert "azure/setup-helm" not in text
