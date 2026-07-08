from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import (
    CleanupStack,
    ReleaseCli,
    assert_no_traceback,
    assert_ok,
    cleanup_helm_release,
    cleanup_kubernetes_namespace,
    require_executable,
    run_external,
)


def require_kubernetes_cluster() -> None:
    """Fail clearly when kubectl cannot reach a cluster."""
    require_executable("kubectl")
    run_external(["kubectl", "version", "--client"], timeout=60)
    result = run_external(["kubectl", "cluster-info"], check=False, timeout=60)
    if result.returncode != 0:
        pytest.fail(
            "Release validation requires kubectl access to a real Kubernetes cluster.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_kubernetes
def test_kubernetes_manifest_release_flow_against_real_cluster(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
) -> None:
    """Render, apply, inspect, and delete Kubernetes manifests in a real namespace."""
    require_kubernetes_cluster()
    namespace = f"sftpwarden-{unique_name}"
    context_name = f"kube-{unique_name}"
    root = tmp_path / "kube-project"
    cleanup_stack.add(lambda: cleanup_kubernetes_namespace(namespace))

    assert_ok(
        cli.run(
            "init",
            context_name,
            "--root",
            root,
            "--deploy",
            "kube",
            "--namespace",
            namespace,
            "--yes",
            timeout=180,
        )
    )
    render = cli.run("kube", "render", "--context", context_name)
    dry_apply = cli.run("kube", "apply", "--context", context_name, "--dry-run", "--json")
    apply = cli.run("kube", "apply", "--context", context_name, "--json", timeout=240)
    status = cli.run("kube", "status", "--context", context_name, "--json", timeout=120)
    doctor = cli.run("kube", "doctor", "--context", context_name, "--json", timeout=120)
    refresh = cli.run("refresh", "--context", context_name, "--dry-run", "--json")
    logs = cli.run("kube", "logs", "--context", context_name, timeout=60)
    delete = cli.run("kube", "delete", "--context", context_name, "--yes", timeout=180)

    assert_ok(render)
    assert "StatefulSet" in render.output
    assert_ok(dry_apply)
    assert json.loads(dry_apply.stdout)["plan"]["mode"] == "manifests"
    assert_ok(apply)
    assert json.loads(apply.stdout)["plan"]["namespace"] == namespace
    assert_ok(status)
    status_data = json.loads(status.stdout)
    assert status_data["namespace"] == namespace
    assert {check["name"] for check in status_data["checks"]} >= {"namespace", "statefulset"}
    assert_ok(doctor)
    assert_ok(refresh)
    assert "kubectl" in json.loads(refresh.stdout)["targets"][0]["result"]
    assert_no_traceback(logs)
    assert_ok(delete)


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_kubernetes
@pytest.mark.release_helm
def test_helm_release_flow_against_real_cluster(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
) -> None:
    """Render, lint, install, inspect, and uninstall the Helm chart in a real namespace."""
    require_kubernetes_cluster()
    require_executable("helm")
    run_external(["helm", "version"], timeout=60)

    namespace = f"sftpwarden-{unique_name}"
    context_name = f"helm-{unique_name}"
    root = tmp_path / "helm-project"
    cleanup_stack.add(lambda: cleanup_kubernetes_namespace(namespace))
    cleanup_stack.add(lambda: cleanup_helm_release(context_name, namespace))

    assert_ok(
        cli.run(
            "init",
            context_name,
            "--root",
            root,
            "--deploy",
            "helm",
            "--namespace",
            namespace,
            "--yes",
            timeout=180,
        )
    )
    values = cli.run("helm", "values", "--context", context_name, "--write")
    template = cli.run("helm", "template", "--context", context_name, "--json", timeout=120)
    lint = cli.run("helm", "lint", "--context", context_name, timeout=120)
    dry_upgrade = cli.run(
        "helm",
        "upgrade",
        "--context",
        context_name,
        "--install",
        "--dry-run",
        "--json",
        timeout=120,
    )
    upgrade = cli.run(
        "helm",
        "upgrade",
        "--context",
        context_name,
        "--install",
        "--json",
        timeout=240,
    )
    status = cli.run("kube", "status", "--context", context_name, "--json", timeout=120)
    uninstall = cli.run("helm", "uninstall", "--context", context_name, "--yes", timeout=180)

    assert_ok(values)
    assert (root / "values.yaml").exists()
    assert_ok(template)
    assert "StatefulSet" in json.loads(template.stdout)["output"]
    assert_ok(lint)
    assert_ok(dry_upgrade)
    assert json.loads(dry_upgrade.stdout)["plan"]["mode"] == "helm"
    assert_ok(upgrade)
    assert json.loads(upgrade.stdout)["plan"]["namespace"] == namespace
    assert_ok(status)
    assert json.loads(status.stdout)["namespace"] == namespace
    assert_ok(uninstall)
