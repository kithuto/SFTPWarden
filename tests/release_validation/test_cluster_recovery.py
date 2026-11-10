"""Real cluster recovery and configuration activation with pinned SFTP identity."""

from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
from uuid import uuid4

import pytest

from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    default_project_config,
    load_config,
    write_config,
)
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.services import backup_kubernetes, restore_kubernetes
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.services.deploy import apply_kubernetes_deployment, kubectl_command
from sftpwarden.services.kubernetes_identity import (
    _capture,
    decode_keys,
    get_resource,
    pin_context,
    request,
    write_resource,
)
from sftpwarden.users import ProviderUsers, SFTPUser

from .conftest import eventually, free_tcp_port, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_fresh_recovery import transfer
from .test_kubernetes_helm import require_kubernetes_cluster

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_kubernetes,
]


def tunnel(config, server, cleanup_stack):
    """Bound a real port-forward process and keep the previously pinned host key."""
    port = free_tcp_port()
    processes = []

    def close():
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    cleanup_stack.add(close)
    command = kubectl_command(
        config,
        [
            "port-forward",
            "--address",
            "127.0.0.1",
            "pod/" + config.kubernetes.release + "-0",
            f"{port}:22",
        ],
        namespace=config.kubernetes.namespace,
    )
    processes.append(
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    )

    def ready():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False

    eventually(ready, timeout_seconds=30, description="disposable cluster SFTP tunnel")
    server.port = port
    if server.known_hosts.exists():
        public = server.known_hosts.read_text().split(" ", 1)[1]
        server.known_hosts.write_text(f"[127.0.0.1]:{port} {public}")
    return close


@pytest.mark.parametrize("mode", [KubernetesMode.MANIFESTS, KubernetesMode.HELM])
@pytest.mark.parametrize("authority", ["generated", "secret"])
def test_cluster_capture_fresh_restore_and_configuration_activation(
    tmp_path,
    monkeypatch,
    policy_image,
    cleanup_stack,
    mode,
    authority,
):
    require_kubernetes_cluster()
    cleanup_stack.add(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(backup_kubernetes, "remote_preparation_image", lambda: policy_image)
    monkeypatch.setattr(restore_kubernetes, "remote_preparation_image", lambda: policy_image)
    client = tmp_path / "client"
    client.mkdir(mode=0o700)
    server = PolicyRuntime(client, policy_image, sync=True)
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    config = default_project_config("cluster-recovery")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = mode
    config.kubernetes.namespace = "sftpwarden-capture-" + uuid4().hex[:12]
    config.kubernetes.release = "runtime"
    config.kubernetes.kube_context = os.environ.get("SFTPWARDEN_RELEASE_KUBE_CONTEXT")
    config = pin_context(config)
    config.docker.image = policy_image
    config.auth.allow_password = False
    config.healthcheck.interval_seconds = 1
    config.sync.interval_seconds = 5
    for namespace in (config.kubernetes.namespace, config.kubernetes.namespace + "-recovered"):
        cleanup_stack.add(
            lambda namespace=namespace: run_external(
                kubectl_command(
                    config,
                    [
                        "delete",
                        "namespace",
                        namespace,
                        "--ignore-not-found",
                        "--wait=true",
                        "--timeout=150s",
                    ],
                ),
                timeout=180,
            )
        )
    material = {}
    if authority == "secret":
        config.kubernetes.host_keys_secret = "operator-identity"
        for kind in ("ed25519", "rsa", "ecdsa"):
            key = client / ("ssh_host_" + kind + "_key")
            run_external(["ssh-keygen", "-q", "-t", kind, "-N", "", "-f", str(key)], timeout=30)
            material[key.name] = base64.b64encode(key.read_bytes()).decode()
            material[key.name + ".pub"] = base64.b64encode(
                key.with_name(key.name + ".pub").read_bytes()
            ).decode()
        for namespace in (config.kubernetes.namespace, config.kubernetes.namespace + "-recovered"):
            selected = config.model_copy(deep=True)
            selected.kubernetes.namespace = namespace
            write_resource(
                selected,
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
                action="create",
            )
            write_resource(
                selected,
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "operator-identity", "namespace": namespace},
                    "type": "Opaque",
                    "data": material,
                },
                action="create",
            )
    write_config(root / "sftpwarden.yaml", config)
    provider_from_config(root, config).write(
        ProviderUsers(
            schema_version=2,
            users=[
                SFTPUser(username="alice", uid=2001, gid=2001, public_keys=[server.alice_public]),
                SFTPUser(
                    username="control", uid=2002, gid=2002, public_keys=[server.control_public]
                ),
            ],
        )
    )
    source_context = local_context("source", root, config.provider.type)
    save_registry(ContextRegistry(default="source", contexts={"source": source_context}))
    apply_kubernetes_deployment(source_context, config)
    fingerprints = _capture(
        config,
        get_resource(config, "statefulset", "runtime"),
        get_resource(config, "pod", "runtime-0"),
    )[1]
    if authority == "secret":
        assert fingerprints == decode_keys(material)[1]
    public = (
        request(
            config,
            [
                "exec",
                "runtime-0",
                "-c",
                "sftpwarden",
                "--",
                "cat",
                config.server.host_keys_dir + "/ssh_host_ed25519_key.pub",
            ],
        )
        .decode()
        .strip()
    )
    close = tunnel(config, server, cleanup_stack)
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("control").returncode == 0
    payload = client / "payload.txt"
    payload.write_bytes(b"private cluster recovery bytes\n")
    batch = client / "transfer.batch"
    batch.write_text(
        f'put "{payload.as_posix()}" upload/payload.txt\nchmod 600 upload/payload.txt\n'
    )
    transfer(server, batch)
    close()
    archive = tmp_path / "cluster.tgz"
    create_backup(context_name="source", output=str(archive), include_data=True, maintenance=True)
    close = tunnel(config, server, cleanup_stack)
    assert server.connect("control").returncode == 0
    close()

    recovered = tmp_path / "recovered"
    restore_backup(
        context_name=None, backup_path=str(archive), target_root=str(recovered), include_data=True
    )
    destination = load_config(recovered / "sftpwarden.yaml")
    destination.deploy.target = DeployTarget.KUBERNETES
    destination.kubernetes.namespace = config.kubernetes.namespace + "-recovered"
    destination.kubernetes.mode = mode
    write_config(recovered / "sftpwarden.yaml", destination)
    target_context = local_context("recovered", recovered, destination.provider.type)
    save_registry(
        ContextRegistry(
            default="recovered", contexts={"source": source_context, "recovered": target_context}
        )
    )
    result = restore_backup(context_name="recovered", backup_path=str(archive), include_data=True)
    assert result.target_state == "empty" and result.safety_backup is None
    assert get_resource(destination, "statefulset", "runtime") is None
    apply_kubernetes_deployment(target_context, destination)
    close = tunnel(destination, server, cleanup_stack)
    assert server.connect("control").returncode == 0
    downloaded = client / "downloaded.txt"
    batch.write_text(f'get upload/payload.txt "{downloaded.as_posix()}"\n')
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    assert (
        request(
            destination,
            [
                "exec",
                "runtime-0",
                "-c",
                "sftpwarden",
                "--",
                "stat",
                "-c",
                "%u:%g:%a",
                destination.server.data_dir + "/alice/upload/payload.txt",
            ],
        )
        .decode()
        .strip()
        == "2001:2001:600"
    )
    previous = get_resource(destination, "pod", "runtime-0")["metadata"]["uid"]
    destination.logging.format = "text"
    write_config(recovered / "sftpwarden.yaml", destination)
    close()
    apply_kubernetes_deployment(target_context, destination)
    assert get_resource(destination, "pod", "runtime-0")["metadata"]["uid"] != previous
    tunnel(destination, server, cleanup_stack)
    assert server.connect("control").returncode == 0
    assert (
        _capture(
            destination,
            get_resource(destination, "statefulset", "runtime"),
            get_resource(destination, "pod", "runtime-0"),
        )[1]
        == fingerprints
    )
