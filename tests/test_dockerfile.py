from __future__ import annotations

from pathlib import Path


def test_dockerfile_keeps_runtime_lightweight() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "EXPOSE 22" in dockerfile
    assert "apk add --no-cache" in dockerfile
    assert "build-base" not in dockerfile
    assert "gcc" not in dockerfile
    assert "apt-get" not in dockerfile
