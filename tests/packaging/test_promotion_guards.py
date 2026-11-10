"""Conflicting or unverified publications must never be treated as missing artifacts."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def promotion_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("tools").resolve()))
    return SimpleNamespace(
        **{
            name: importlib.import_module(name)
            for name in (
                "promote_image",
                "promote_index",
                "promote_chart",
                "pypi_candidate",
                "github_candidate",
            )
        }
    )


@pytest.mark.parametrize(
    "diagnostic",
    [
        "unauthorized: manifest unknown",
        "denied: not found",
        "forbidden",
        "DNS host not found",
        "connection refused",
    ],
)
def test_registry_failure_is_not_absence(promotion_tools, monkeypatch, diagnostic):
    module = promotion_tools.promote_image
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, "", diagnostic)
    )
    with pytest.raises(RuntimeError, match="absence was not established"):
        module.remote_manifest("registry.invalid/test:1.3.1")


@pytest.mark.parametrize("existing", ["same", "different", "missing", "bad-readback"])
def test_versioned_image_conflicts_and_partial_publication(promotion_tools, monkeypatch, existing):
    module = promotion_tools.promote_image
    commands = []

    def run(command):
        commands.append(command)
        return "sha256:candidate" if command[1:3] == ["image", "inspect"] else ""

    manifests = {
        "same": [{"config": {"digest": "sha256:candidate"}}],
        "different": [{"config": {"digest": "sha256:other"}}],
        "missing": [None, {"config": {"digest": "sha256:candidate"}}],
        "bad-readback": [None, {"config": {"digest": "sha256:other"}}],
    }[existing]
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(module, "remote_manifest", lambda ref: manifests.pop(0))
    if existing in {"different", "bad-readback"}:
        with pytest.raises(RuntimeError):
            module.promote("candidate", "versioned")
    else:
        module.promote("candidate", "versioned")
    assert any(command[1] == "push" for command in commands) == (
        existing in {"missing", "bad-readback"}
    )


@pytest.mark.parametrize(
    "platforms", [["amd64", "amd64"], ["amd64", "riscv64"], ["windows", "arm64"]]
)
def test_index_refuses_unverified_platforms(promotion_tools, platforms):
    document = {
        "manifests": [
            {"digest": "sha256:" + arch, "platform": {"os": "linux", "architecture": arch}}
            for arch in platforms
        ]
    }
    with pytest.raises(RuntimeError, match="platform"):
        promotion_tools.promote_index.identities(document)


def test_index_checks_child_platform_before_publishing(promotion_tools, monkeypatch):
    module = promotion_tools.promote_index
    commands = []

    def run(command):
        commands.append(command)
        if "{{json .Manifest}}" in command:
            return json.dumps({"digest": "sha256:" + command[-1].rsplit("-", 1)[1]})
        return json.dumps({"architecture": "amd64", "os": "linux"})

    monkeypatch.setattr(module, "run", run)
    with pytest.raises(RuntimeError, match="native platform"):
        module.publish("registry.invalid/test", "1.3.1")
    assert not any("create" in command for command in commands)


def test_pypi_partial_publication_only_stages_missing_exact_bytes(
    promotion_tools, monkeypatch, tmp_path
):
    module = promotion_tools.pypi_candidate
    wheel = tmp_path / "sftpwarden-1.3.1-py3-none-any.whl"
    source = tmp_path / "sftpwarden-1.3.1.tar.gz"
    wheel.write_bytes(b"wheel")
    source.write_bytes(b"sdist")
    published = {wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()}
    monkeypatch.setattr(module, "published_files", lambda version: published)
    stage = tmp_path / "missing"
    assert module.check(tmp_path, "1.3.1", output=stage) == 1
    assert [p.name for p in stage.iterdir()] == [source.name]
    with pytest.raises(module.PublicationPending):
        module.check(tmp_path, "1.3.1")
    published[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    assert module.check(tmp_path, "1.3.1") == 0
    published[wheel.name] = "changed"
    with pytest.raises(ValueError, match="differs"):
        module.check(tmp_path, "1.3.1", output=tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_chart_conflict_never_pushes(promotion_tools, monkeypatch, tmp_path):
    module = promotion_tools.promote_chart
    candidate, published = tmp_path / "candidate.tgz", tmp_path / "published.tgz"
    candidate.write_bytes(b"candidate")
    published.write_bytes(b"different")
    monkeypatch.setattr(module, "pull", lambda *a: published)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: pytest.fail("pushed conflicting chart")
    )
    with pytest.raises(RuntimeError, match="different bytes"):
        module.promote(candidate, "oci://registry.invalid/charts", "1.3.1")
    published.write_bytes(candidate.read_bytes())
    module.promote(candidate, "oci://registry.invalid/charts", "1.3.1")


def test_github_tag_conflict_refuses_release_reuse(promotion_tools, monkeypatch, tmp_path):
    module = promotion_tools.github_candidate
    monkeypatch.setattr(
        module, "request", lambda path: {"object": {"type": "commit", "sha": "b" * 40}}
    )
    with pytest.raises(RuntimeError, match="different commit"):
        module.check(tmp_path, "owner/repository", "1.3.1", "a" * 40)


@pytest.mark.parametrize("digest", [None, "sha256:different"])
def test_github_asset_missing_or_conflicting_digest_stops_promotion(
    promotion_tools, monkeypatch, tmp_path, digest
):
    module = promotion_tools.github_candidate
    (tmp_path / "artifact-manifest.json").write_text("{}")

    def request(path):
        if "/git/ref/" in path:
            return {"object": {"type": "commit", "sha": "a" * 40}}
        return {"assets": [{"name": "artifact-manifest.json", "digest": digest}]}

    monkeypatch.setattr(module, "request", request)
    with pytest.raises(RuntimeError, match="assets differ"):
        module.check(tmp_path, "owner/repository", "1.3.1", "a" * 40)
