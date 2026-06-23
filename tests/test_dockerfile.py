from __future__ import annotations

from pathlib import Path


def test_dockerfile_keeps_runtime_lightweight() -> None:
    dockerfile = Path("docker/runtime/Dockerfile").read_text(encoding="utf-8")

    assert "EXPOSE 22" in dockerfile
    assert "apk add --no-cache" in dockerfile
    assert "build-base" not in dockerfile
    assert "gcc" not in dockerfile
    assert "apt-get" not in dockerfile
    assert "tini" in dockerfile
    assert "--no-cache-dir" in dockerfile
    assert "--no-compile" in dockerfile
    assert "/usr/local/bin/pip*" in dockerfile
    assert "site-packages/pip*" in dockerfile
    assert "__pycache__" in dockerfile
    assert "*.pyc" in dockerfile
    assert "COPY sftpwarden ./sftpwarden" in dockerfile
    assert "docker/runtime/entrypoint.sh" in dockerfile
    assert "docker/runtime/sshd_config.template" in dockerfile
    assert not Path("Dockerfile").exists()
    assert not Path("docker/entrypoint.sh").exists()
    assert not Path("docker/sshd_config").exists()


def test_watcher_dockerfile_uses_flat_package_layout() -> None:
    dockerfile = Path("docker/watcher/Dockerfile").read_text(encoding="utf-8")

    assert "COPY sftpwarden ./sftpwarden" in dockerfile
    assert "--no-cache-dir --no-compile" in dockerfile
    assert "/usr/local/bin/pip*" in dockerfile
    assert "site-packages/pip*" in dockerfile
    assert "__pycache__" in dockerfile


def test_release_workflows_exist() -> None:
    workflows = {path.name for path in Path(".github/workflows").glob("*.yml")}

    assert workflows == {
        "ci.yml",
        "docker.yml",
        "docs.yml",
        "release.yml",
        "security.yml",
    }


def test_public_repository_templates_exist() -> None:
    expected = [
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/feature_request.md",
    ]

    for path in expected:
        assert Path(path).exists(), f"missing public repository file: {path}"
