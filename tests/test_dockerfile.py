from __future__ import annotations

from pathlib import Path


def test_dockerfile_keeps_runtime_lightweight() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    canonical = Path("docker/runtime/Dockerfile").read_text(encoding="utf-8")

    assert "EXPOSE 22" in dockerfile
    assert "EXPOSE 22" in canonical
    assert "apk add --no-cache" in dockerfile
    assert "apk add --no-cache" in canonical
    assert "build-base" not in dockerfile
    assert "build-base" not in canonical
    assert "gcc" not in dockerfile
    assert "gcc" not in canonical
    assert "apt-get" not in dockerfile
    assert "apt-get" not in canonical
    assert "tini" in dockerfile
    assert "tini" in canonical
    assert "--no-cache-dir" in dockerfile
    assert "--no-cache-dir" in canonical
    assert "COPY sftpwarden ./sftpwarden" in dockerfile
    assert "COPY sftpwarden ./sftpwarden" in canonical
    assert "docker/runtime/entrypoint.sh" in dockerfile
    assert "docker/runtime/sshd_config.template" in dockerfile
    assert not Path("docker/entrypoint.sh").exists()
    assert not Path("docker/sshd_config").exists()


def test_watcher_dockerfile_uses_flat_package_layout() -> None:
    dockerfile = Path("docker/watcher/Dockerfile").read_text(encoding="utf-8")

    assert "COPY sftpwarden ./sftpwarden" in dockerfile


def test_release_workflows_exist() -> None:
    workflows = {path.name for path in Path(".github/workflows").glob("*.yml")}

    assert workflows == {
        "ci.yml",
        "docker.yml",
        "docs.yml",
        "release.yml",
        "security.yml",
    }
