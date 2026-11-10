from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sftpwarden.config import DeployTarget, load_config, write_config

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


def select_candidate(root: Path) -> None:
    """Use the installed candidate image and explicitly selected disposable cluster."""
    path = root / "sftpwarden.yaml"
    config = load_config(path)
    if image := os.environ.get("SFTPWARDEN_RELEASE_RUNTIME_IMAGE"):
        config.docker.image = image
    if context := os.environ.get("SFTPWARDEN_RELEASE_KUBE_CONTEXT"):
        config.kubernetes.kube_context = context
    write_config(path, config)


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_kubernetes
@pytest.mark.parametrize("use_secret", [False, True], ids=["pvc", "custom-secret"])
def test_kube_doctor_identity_against_real_cluster(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
    use_secret: bool,
) -> None:
    """Check resource discovery and missing-resource diagnostics without a runtime image."""
    require_executable("kubectl")
    kube_context = (
        os.environ.get("SFTPWARDEN_RELEASE_KUBE_CONTEXT")
        or run_external(["kubectl", "config", "current-context"]).stdout.strip()
    )
    kubectl = ["kubectl", "--context", kube_context]
    run_external([*kubectl, "cluster-info"], timeout=60)
    namespace = f"doctor-{unique_name}"

    def cleanup() -> None:
        run_external(
            [
                *kubectl,
                "delete",
                "namespace",
                namespace,
                "--ignore-not-found=true",
                "--wait=true",
                "--timeout=60s",
            ],
            timeout=90,
        )
        remaining = run_external(
            [*kubectl, "get", "namespace", namespace, "--ignore-not-found=true", "-o", "name"]
        )
        assert not remaining.stdout.strip(), "Doctor test namespace was not removed"

    cleanup_stack.add(cleanup)
    run_external([*kubectl, "create", "namespace", namespace])
    context_name = f"doctor-{unique_name}"
    root = tmp_path / "doctor-project"
    assert_ok(cli.run("init", context_name, "--root", root, "--yes"))
    config = load_config(root / "sftpwarden.yaml")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.namespace = namespace
    config.kubernetes.kube_context = kube_context
    config.kubernetes.release = context_name
    config.kubernetes.host_keys_secret = "custom-identity" if use_secret else None
    write_config(root / "sftpwarden.yaml", config)
    kind = "secret" if use_secret else "pvc"
    name = "custom-identity" if use_secret else f"{context_name}-host-keys"
    check_name = "host-keys secret" if use_secret else "host-keys PVC"
    namespaced = [*kubectl, "-n", namespace]
    # A legacy Secret must not hide an absent configured identity resource.
    run_external([*namespaced, "create", "secret", "generic", f"{context_name}-host-keys"])
    resource = {
        "apiVersion": "v1",
        "kind": "Secret" if use_secret else "PersistentVolumeClaim",
        "metadata": {"name": name, "namespace": namespace},
    }
    if not use_secret:
        # Existence is the diagnostic contract. Do not provision real storage.
        resource["spec"] = {
            "storageClassName": "",
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "1Mi"}},
            "volumeName": f"unallocated-{unique_name}",
        }
    resource_path = tmp_path / "identity.json"
    resource_path.write_text(json.dumps(resource), encoding="utf-8")
    for present in (False, True, False):
        if present:
            run_external([*namespaced, "apply", "-f", str(resource_path)])
        else:
            run_external([*namespaced, "delete", kind, name, "--ignore-not-found=true"])
        result = cli.run("kube", "doctor", "--context", context_name, "--json")
        assert_ok(result)
        checks = json.loads(result.stdout)["checks"]
        identity = next(check for check in checks if check["name"] == check_name)
        assert identity["command"] == [*namespaced, "get", kind, name]
        assert (identity["returncode"] == 0) is present, identity
        assert bool(identity["message"]) is not present
        assert all(check["returncode"] == 0 for check in checks if check is not identity)
        text = cli.run("kube", "doctor", "--context", context_name)
        assert_ok(text)
        assert f"{check_name}: {'pass' if present else 'warn'}" in text.stdout


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
    select_candidate(root)
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
    assert all(check["returncode"] == 0 for check in json.loads(doctor.stdout)["checks"])
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
    select_candidate(root)
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
