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


def test_github_actions_are_not_created_yet() -> None:
    assert not Path(".github").exists()
