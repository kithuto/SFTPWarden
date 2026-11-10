"""Exercise publication and retry guards against an owned loopback OCI registry."""

from __future__ import annotations

import importlib
import os
import subprocess
import tarfile
from uuid import uuid4

import pytest

from .conftest import (
    REPO_ROOT,
    docker_image_id,
    eventually,
    preserve_or_remove_image,
    remove_docker_container,
    run_external,
)

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_real_image_index_and_chart_publication_is_repeatable_and_refuses_conflicts(
    tmp_path, monkeypatch, cleanup_stack
):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "tools"))
    images = importlib.import_module("promote_image")
    indexes = importlib.import_module("promote_index")
    charts = importlib.import_module("promote_chart")
    name = "sftpwarden-publication-" + uuid4().hex[:10]
    previous = docker_image_id("registry:2")
    cleanup_stack.add(lambda: preserve_or_remove_image("registry:2", previous))
    cleanup_stack.add(lambda: remove_docker_container(name))
    run_external(
        ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1:0:5000", "registry:2"],
        timeout=180,
    )
    port = run_external(["docker", "port", name, "5000/tcp"]).stdout.strip().rsplit(":", 1)[1]
    host = f"127.0.0.1:{port}"
    eventually(
        lambda: (
            run_external(
                ["docker", "exec", name, "wget", "-qO-", "http://127.0.0.1:5000/v2/"],
                check=False,
            ).returncode
            == 0
        ),
        timeout_seconds=30,
        description="owned publication registry",
    )
    # Only this disposable loopback registry uses HTTP. All commands still run
    # against the real registry; no manifests or command results are fabricated.
    real_run = subprocess.run

    def local_transport(command, *args, **kwargs):
        if command[0] == "helm" and command[1] in {"push", "pull"}:
            assert any(f"oci://{host}/" in str(part) for part in command)
            command = [*command, "--plain-http"]
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", local_transport)
    archive = tmp_path / "rootfs.tar"
    with tarfile.open(archive, "w"):
        pass
    repository = f"{host}/sftpwarden"
    candidate = os.environ.get("SFTPWARDEN_RELEASE_RUNTIME_IMAGE")
    candidate_arch = (
        run_external(
            ["docker", "image", "inspect", "--format", "{{.Architecture}}", candidate]
        ).stdout.strip()
        if candidate
        else None
    )
    sources = []
    for arch in ("amd64", "arm64"):
        source = f"{name}:{arch}"
        target = f"{repository}:1.4.0-{arch}"
        cleanup_stack.add(lambda source=source: preserve_or_remove_image(source, None))
        cleanup_stack.add(lambda target=target: preserve_or_remove_image(target, None))
        if candidate and arch == candidate_arch:
            # CI promotes the actual tested runtime on its native architecture.
            # Only the other platform uses a small synthetic config/layer fixture.
            run_external(["docker", "tag", candidate, source])
        else:
            run_external(
                ["docker", "image", "import", "--platform", f"linux/{arch}", str(archive), source]
            )
        sources.append(source)
        assert images.remote_manifest(target) is None
        images.promote(source, target)
        images.promote(source, target)
    indexes.publish(repository, "1.4.0")
    indexes.publish(repository, "1.4.0", latest=True)
    expected = indexes.identities(images.remote_manifest(f"{repository}:1.4.0"))
    assert set(expected) == {"amd64", "arm64"}
    assert indexes.identities(images.remote_manifest(f"{repository}:latest")) == expected
    with pytest.raises(RuntimeError, match="different bytes"):
        images.promote(sources[1], f"{repository}:1.4.0-amd64")
    run_external(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"{repository}:1.4.0",
            f"{repository}:1.4.0-arm64",
        ]
    )
    with pytest.raises(RuntimeError, match="different images"):
        indexes.publish(repository, "1.4.0", latest=True)
    assert indexes.identities(images.remote_manifest(f"{repository}:latest")) == expected
    run_external(
        ["helm", "package", str(REPO_ROOT / "charts/sftpwarden"), "--destination", str(tmp_path)]
    )
    chart = next(tmp_path.glob("sftpwarden-*.tgz"))
    version = chart.name.removeprefix("sftpwarden-").removesuffix(".tgz")
    chart_repository = f"oci://{host}/charts"
    charts.promote(chart, chart_repository, version)
    charts.promote(chart, chart_repository, version)
    chart.write_bytes(chart.read_bytes() + b"different")
    with pytest.raises(RuntimeError, match="different bytes"):
        charts.promote(chart, chart_repository, version)
