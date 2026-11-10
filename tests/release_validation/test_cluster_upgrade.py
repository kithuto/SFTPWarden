"""Legacy Pod identity is prepared before direct Helm and manifest activation."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tarfile
from uuid import uuid4

import pytest
import yaml

from sftpwarden.config import DeployTarget, KubernetesMode, default_project_config, write_config
from sftpwarden.contexts import local_context
from sftpwarden.providers import provider_from_config
from sftpwarden.render.kubernetes import helm_values_model, kubernetes_manifests
from sftpwarden.services.deploy import (
    LOCAL_CHART_PATH,
    apply_kubernetes_deployment,
    helm_command,
    kubectl_command,
)
from sftpwarden.services.deployment_upgrade import begin_upgrade, complete_upgrade, plan_upgrade
from sftpwarden.services.kubernetes_identity import (
    _capture,
    ensure_release_retention,
    get_resource,
    pin_context,
    prepare_identity,
    request,
    wait_for_activation,
    write_resource,
)
from sftpwarden.services.kubernetes_lock import operation_lock
from sftpwarden.users import ProviderUsers, SFTPUser

from .conftest import REPO_ROOT, eventually, remove_docker_container, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_cluster_recovery import tunnel
from .test_fresh_recovery import transfer
from .test_kubernetes_helm import require_kubernetes_cluster
from .test_upgrade_recovery import LEGACY_COMMIT
from .test_upgrade_recovery import legacy_image as legacy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_kubernetes,
    pytest.mark.release_upgrade,
]


@pytest.fixture
def legacy_chart(tmp_path):
    archive = tmp_path / "legacy-chart.tar"
    with archive.open("xb") as output:
        result = subprocess.run(
            ["git", "archive", LEGACY_COMMIT, "charts/sftpwarden"],
            cwd=REPO_ROOT,
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    assert result.returncode == 0, (
        "Fetch the recorded 1.3.0 release commit before this upgrade gate."
    )
    root = tmp_path / "legacy-source"
    root.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(root, filter="data")
    chart = root / "charts" / "sftpwarden"
    assert yaml.safe_load((chart / "Chart.yaml").read_text())["version"] == "1.3.0"
    return chart


@pytest.mark.parametrize(
    "mode,authority",
    [
        (KubernetesMode.MANIFESTS, "generated"),
        (KubernetesMode.MANIFESTS, "external"),
        (KubernetesMode.HELM, "generated"),
        (KubernetesMode.HELM, "legacy-secret"),
        (KubernetesMode.HELM, "external"),
    ],
)
def test_legacy_cluster_preparation_direct_upgrade_and_retained_reinstall(
    tmp_path,
    policy_image,
    legacy_image,
    legacy_chart,
    cleanup_stack,
    monkeypatch,
    mode,
    authority,
):
    require_kubernetes_cluster()
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir(mode=0o700)
    client = tmp_path / "client"
    client.mkdir(mode=0o700)
    server = PolicyRuntime(client, policy_image, sync=True)
    config = default_project_config("legacy-cluster")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = mode
    config.kubernetes.release = "runtime"
    config.kubernetes.namespace = "sftpwarden-upgrade-" + uuid4().hex[:12]
    config.kubernetes.kube_context = os.environ.get("SFTPWARDEN_RELEASE_KUBE_CONTEXT")
    config = pin_context(config)
    config.docker.image = legacy_image
    config.auth.allow_password = False
    config.sync.interval_seconds = 5
    write_config(root / "sftpwarden.yaml", config)
    provider_from_config(root, config).write(
        ProviderUsers(
            schema_version=2,
            users=[
                SFTPUser(username="alice", uid=2201, gid=2201, public_keys=[server.alice_public]),
                SFTPUser(
                    username="control", uid=2202, gid=2202, public_keys=[server.control_public]
                ),
            ],
        )
    )
    cleanup_stack.add(
        lambda: run_external(
            kubectl_command(
                config,
                [
                    "delete",
                    "namespace",
                    config.kubernetes.namespace,
                    "--ignore-not-found",
                    "--wait=true",
                    "--timeout=150s",
                ],
            ),
            timeout=180,
        )
    )
    write_resource(
        config,
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": config.kubernetes.namespace},
        },
        action="create",
    )
    values = helm_values_model(config, root)
    values["hostKeys"]["secretName"] = "runtime-host-keys"
    # These are real old chart resources. New-only null model fields are omitted
    # from sftpwardenConfig by the normal config serializer.
    old_values = root / "old-values.yaml"
    old_values.write_text(yaml.safe_dump(values))
    if mode == KubernetesMode.HELM:
        run_external(
            helm_command(
                config,
                [
                    "install",
                    "runtime",
                    str(legacy_chart),
                    "--namespace",
                    config.kubernetes.namespace,
                    "--values",
                    str(old_values),
                    "--wait",
                    "--timeout",
                    "180s",
                ],
            ),
            timeout=210,
        )
    else:
        # Exercise the actual 1.3.0 manifest deployment, not chart resources:
        # Helm uses a different immutable StatefulSet selector. Switching from
        # a chart to CLI manifests is not an in-place version upgrade.
        renderer = "sftpwarden-legacy-render-" + uuid4().hex[:12]
        cleanup_stack.add(lambda: remove_docker_container(renderer))
        rendered = run_external(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                renderer,
                "--platform",
                "linux/amd64",
                "--mount",
                f"type=bind,src={root},dst=/project,readonly",
                "--entrypoint",
                "python",
                legacy_image,
                "-c",
                "from sftpwarden.config import load_config; "
                "from sftpwarden.render.kubernetes import kubernetes_manifest_text; "
                "print(kubernetes_manifest_text("
                "load_config('/project/sftpwarden.yaml'), '/project'))",
            ],
            timeout=60,
        ).stdout
        for document in yaml.safe_load_all(rendered):
            if document:
                # The old CLI used kubectl apply, retaining last-applied state.
                write_resource(config, document)
        request(config, ["wait", "--for=condition=Ready", "pod/runtime-0", "--timeout=180s"])
    workload = get_resource(config, "statefulset", "runtime")
    old_pod = get_resource(config, "pod", "runtime-0")
    material, fingerprints = _capture(config, workload, old_pod)
    public = base64.b64decode(material["ssh_host_ed25519_key.pub"]).decode().strip()
    close = tunnel(config, server, cleanup_stack)
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("control").returncode == 0
    payload = client / "payload"
    payload.write_bytes(b"retained during legacy cluster upgrade\n")
    batch = client / "transfer.batch"
    batch.write_text(f'put "{payload.as_posix()}" upload/payload\nchmod 600 upload/payload\n')
    transfer(server, batch)
    if authority == "legacy-secret":
        secret = get_resource(config, "secret", "runtime-host-keys")
        secret["data"] = material
        write_resource(config, secret, action="replace")
        config.kubernetes.host_keys_secret = "runtime-host-keys"
    elif authority == "external":
        write_resource(
            config,
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "operator-identity", "namespace": config.kubernetes.namespace},
                "type": "Opaque",
                "data": material,
            },
            action="create",
        )
        config.kubernetes.host_keys_secret = "operator-identity"
    config.docker.image = policy_image
    write_config(root / "sftpwarden.yaml", config)
    candidate_values = root / "candidate-values.yaml"
    candidate_values.write_text(yaml.safe_dump(helm_values_model(config, root)))
    if mode == KubernetesMode.HELM:
        direct = helm_command(
            config,
            [
                "upgrade",
                "runtime",
                str(LOCAL_CHART_PATH),
                "--namespace",
                config.kubernetes.namespace,
                "--values",
                str(candidate_values),
            ],
        )
        refused = run_external(direct, check=False, timeout=60)
        assert refused.returncode != 0
        assert "identity has not been prepared" in refused.stdout + refused.stderr
        assert (
            get_resource(config, "pod", "runtime-0")["metadata"]["uid"]
            == old_pod["metadata"]["uid"]
        )
        assert server.connect("control").returncode == 0
    entry = local_context("upgrading", root, config.provider.type)
    upgrade = plan_upgrade(entry)
    assert upgrade.source == "1.3.0"
    begin_upgrade(entry, upgrade)
    with operation_lock(config, "test-identity-preflight"):
        prepare_identity(config)
    if mode == KubernetesMode.HELM:
        run_external(direct, timeout=180)
        # OnDelete prevents an unrequested replacement during Helm's resource update.
        assert (
            get_resource(config, "pod", "runtime-0")["metadata"]["uid"]
            == old_pod["metadata"]["uid"]
        )
    else:
        for document in kubernetes_manifests(config, root):
            write_resource(config, document)
    close()
    request(config, ["delete", "pod", "runtime-0", "--wait=true", "--timeout=60s"])
    wait_for_activation(config, previous_uid=old_pod["metadata"]["uid"])
    assert complete_upgrade(entry, upgrade)["status"] == "complete"
    close = tunnel(config, server, cleanup_stack)
    assert server.connect("control").returncode == 0
    assert (
        _capture(
            config,
            get_resource(config, "statefulset", "runtime"),
            get_resource(config, "pod", "runtime-0"),
        )[1]
        == fingerprints
    )
    downloaded = client / "downloaded"
    batch.write_text(f'get upload/payload "{downloaded.as_posix()}"\n')
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    assert (
        request(
            config,
            [
                "exec",
                "runtime-0",
                "-c",
                "sftpwarden",
                "--",
                "stat",
                "-c",
                "%u:%g:%a",
                "/data/alice/upload/payload",
            ],
        )
        .decode()
        .strip()
        == "2201:2201:600"
    )
    # A failed candidate must not require rotating identity or deleting storage.
    verified_workload = get_resource(config, "statefulset", "runtime")
    failed_workload = json.loads(json.dumps(verified_workload))
    failed_workload["spec"]["template"]["spec"]["containers"][0]["command"] = [
        "sh",
        "-c",
        "exit 1",
    ]
    write_resource(config, failed_workload)
    close()
    request(config, ["delete", "pod", "runtime-0", "--wait=true", "--timeout=60s"])

    def candidate_failed():
        pod = get_resource(config, "pod", "runtime-0")
        return pod and any(
            item.get("restartCount", 0) > 0 or item.get("state", {}).get("terminated")
            for item in pod.get("status", {}).get("containerStatuses", [])
        )

    eventually(candidate_failed, timeout_seconds=90, description="candidate activation failure")
    failed_uid = get_resource(config, "pod", "runtime-0")["metadata"]["uid"]
    current = get_resource(config, "statefulset", "runtime")
    current["spec"]["template"] = verified_workload["spec"]["template"]
    write_resource(config, current, action="replace")
    request(config, ["delete", "pod", "runtime-0", "--wait=true", "--timeout=60s"])
    wait_for_activation(config, previous_uid=failed_uid)
    close = tunnel(config, server, cleanup_stack)
    assert server.connect("control").returncode == 0
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    assert (
        _capture(
            config,
            get_resource(config, "statefulset", "runtime"),
            get_resource(config, "pod", "runtime-0"),
        )[1]
        == fingerprints
    )
    claims = {
        name: get_resource(config, "pvc", "runtime-" + name)["metadata"]["uid"]
        for name in ("data", "state", "provider")
    }
    if authority == "generated":
        claims["host-keys"] = get_resource(config, "pvc", "runtime-host-keys")["metadata"]["uid"]
    if mode == KubernetesMode.HELM:
        ensure_release_retention(config)
        close()
        run_external(
            helm_command(
                config,
                [
                    "uninstall",
                    "runtime",
                    "--namespace",
                    config.kubernetes.namespace,
                    "--wait",
                    "--timeout",
                    "90s",
                ],
            ),
            timeout=120,
        )
        for name, uid in claims.items():
            assert get_resource(config, "pvc", "runtime-" + name)["metadata"]["uid"] == uid
        if authority == "legacy-secret":
            assert get_resource(config, "secret", "runtime-host-keys")["data"] == material
        if authority == "external":
            secret = get_resource(config, "secret", "operator-identity")
            assert secret["data"] == material
            assert "meta.helm.sh/release-name" not in secret["metadata"].get("annotations", {})
        entry = local_context("reinstalled", root, config.provider.type)
        apply_kubernetes_deployment(entry, config)
        tunnel(config, server, cleanup_stack)
        assert server.connect("control").returncode == 0
        assert (
            _capture(
                config,
                get_resource(config, "statefulset", "runtime"),
                get_resource(config, "pod", "runtime-0"),
            )[1]
            == fingerprints
        )
