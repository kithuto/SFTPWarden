"""Capture authoritative Kubernetes mounts in a bounded, explicitly stopped window."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sftpwarden.config import FILE_PROVIDER_TYPES, SFTPWardenConfig
from sftpwarden.remote.deploy import remote_preparation_image
from sftpwarden.render.kubernetes import kubernetes_resource_name
from sftpwarden.services.kubernetes_identity import (
    _capture,
    get_resource,
    idle_helper_command,
    pin_context,
    request,
    write_resource,
)
from sftpwarden.services.kubernetes_lock import operation_lock
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_binary_output

if TYPE_CHECKING:
    from sftpwarden.services.backup import BackupResult


_APPLIED = """
import json, os, pathlib
record = json.loads(pathlib.Path('/run/sftpwarden/applied-config.json').read_text())
print(json.dumps({'applied': record, 'environment': dict(os.environ)}))
"""

_PREPARE = r"""
import base64, json, pathlib, sys
from sftpwarden.runtime.host_keys import REQUIRE_EXISTING, prepare_host_keys
from sftpwarden.utils.persistence import atomic_bytes
payload = json.load(sys.stdin)
atomic_bytes(pathlib.Path('/applied.json'), json.dumps(payload['applied']).encode())
environment = [k+'='+v for k,v in payload['environment'].items()]
atomic_bytes(pathlib.Path('/container.json'), json.dumps({'Config': {'Env': environment}}).encode())
root = pathlib.Path(payload['applied']['config']['server']['host_keys_dir'])
atomic_bytes(root / REQUIRE_EXISTING, b'Captured server identity; do not generate another.\n')
for name, content in payload['keys'].items():
    atomic_bytes(root / name, base64.b64decode(content, validate=True))
if prepare_host_keys(root) != payload['fingerprints']:
    raise SystemExit(1)
"""


def capture_specification(
    workload: dict[str, Any],
    pod: dict[str, Any],
    applied: SFTPWardenConfig,
) -> dict[str, Any]:
    """Mount only the selected runtime's authoritative claims, always read-only."""
    specification = workload["spec"]["template"]["spec"]
    runtime = next(item for item in specification["containers"] if item["name"] == "sftpwarden")
    originals = {item["name"]: item for item in specification["volumes"]}
    required = {"state", "data"}
    if applied.provider.type in FILE_PROVIDER_TYPES:
        required.add("provider")
    mounts = []
    volumes = []
    for name in sorted(required):
        source = originals.get(name, {})
        if "persistentVolumeClaim" not in source:
            raise RuntimeError(
                "Recovery capture requires persistent runtime data, state and "
                "file-provider volumes."
            )
        mount = next((item for item in runtime["volumeMounts"] if item["name"] == name), None)
        if mount is None or "subPath" in mount or "subPathExpr" in mount:
            raise RuntimeError(
                "Recovery capture requires complete, inspectable runtime volume mounts."
            )
        volumes.append(source)
        mounts.append({**mount, "readOnly": True})
    volumes.append({"name": "captured-keys", "emptyDir": {"medium": "Memory"}})
    mounts.append({"name": "captured-keys", "mountPath": applied.server.host_keys_dir})
    return {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "activeDeadlineSeconds": 600,
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchFields": [
                                {
                                    "key": "metadata.name",
                                    "operator": "In",
                                    "values": [pod["spec"]["nodeName"]],
                                }
                            ]
                        }
                    ],
                }
            }
        },
        "containers": [
            {
                "name": "capture",
                "image": remote_preparation_image(),
                "command": idle_helper_command(),
                "volumeMounts": mounts,
                "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False},
            }
        ],
        "volumes": volumes,
        **(
            {"imagePullSecrets": specification["imagePullSecrets"]}
            if specification.get("imagePullSecrets")
            else {}
        ),
    }


def create_kubernetes_backup(
    config: SFTPWardenConfig,
    output: Path,
    *,
    include_data: bool,
    maintenance: bool,
    dry_run: bool,
) -> BackupResult:
    """Verify capture and restored availability before publishing sensitive recovery material."""
    from sftpwarden.services.backup import BackupResult
    from sftpwarden.services.deploy import kubectl_command
    from sftpwarden.services.restore_existing import verify_safety_archive

    if not maintenance:
        raise RuntimeError(
            "Kubernetes recovery capture requires --maintenance and paused external writers."
        )
    if dry_run:
        return BackupResult(
            path=output,
            entries=[
                "cluster:applied-config",
                "cluster:provider",
                "cluster:host_keys",
                "cluster:state",
                *(["cluster:data"] if include_data else []),
            ],
        )
    config = pin_context(config)
    name = kubernetes_resource_name(config.kubernetes.release)
    with (
        operation_lock(config, "recovery-capture"),
        tempfile.TemporaryDirectory(prefix="sftpwarden-cluster-capture-") as directory,
    ):
        workload = get_resource(config, "statefulset", name)
        pod = get_resource(config, "pod", name + "-0")
        if workload is None or pod is None or workload["spec"].get("replicas") != 1:
            raise RuntimeError(
                "Capture requires the selected running single-replica workload; a "
                "missing Pod is not empty storage."
            )
        if not any(
            owner.get("uid") == workload["metadata"]["uid"]
            for owner in pod["metadata"].get("ownerReferences", [])
        ):
            raise RuntimeError("The selected Pod does not belong to the inspected workload.")
        payload = json.loads(
            request(
                config,
                [
                    "exec",
                    pod["metadata"]["name"],
                    "-c",
                    "sftpwarden",
                    "--",
                    "python",
                    "-c",
                    _APPLIED,
                ],
            )
        )
        applied = SFTPWardenConfig.model_validate(payload["applied"]["config"])
        payload["keys"], payload["fingerprints"] = _capture(config, workload, pod)
        sources = {item["name"]: item for item in workload["spec"]["template"]["spec"]["volumes"]}
        if "persistentVolumeClaim" not in sources.get("host-keys", {}):
            secret_name = sources.get("host-key-secret", {}).get("secret", {}).get("secretName")
            if not secret_name:
                raise RuntimeError(
                    "Migrate ephemeral host keys before maintenance; restarting this "
                    "legacy Pod would lose identity."
                )
            from sftpwarden.services.kubernetes_identity import decode_keys

            secret = get_resource(config, "secret", secret_name)
            if secret is None or decode_keys(secret.get("data", {}))[1] != payload["fingerprints"]:
                raise RuntimeError(
                    "The restart host-key source does not preserve the running server identity."
                )
        spec = capture_specification(workload, pod, applied)
        nonce = uuid4().hex
        helper = f"{name[:35]}-capture-{nonce[:12]}"
        resume = False
        staged = Path(directory) / "recovery.tgz"
        try:
            # Recheck immutable identity before the optimistic replica-count update.
            current_pod = get_resource(config, "pod", name + "-0")
            if current_pod is None or current_pod["metadata"]["uid"] != pod["metadata"]["uid"]:
                raise RuntimeError("The runtime changed during recovery inspection.")
            resume = True
            request(
                config,
                [
                    "scale",
                    "statefulset",
                    name,
                    "--replicas=0",
                    "--current-replicas=1",
                    "--resource-version=" + workload["metadata"]["resourceVersion"],
                ],
            )
            request(config, ["wait", "--for=delete", f"pod/{name}-0", "--timeout=120s"])
            write_resource(
                config,
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": helper,
                        "namespace": config.kubernetes.namespace,
                        "labels": {"sftpwarden.io/capture-owner": nonce},
                    },
                    "spec": spec,
                },
                action="create",
            )
            request(config, ["wait", "--for=condition=Ready", f"pod/{helper}", "--timeout=150s"])
            request(
                config,
                ["exec", "-i", helper, "-c", "capture", "--", "python", "-c", _PREPARE],
                json.dumps(payload).encode(),
            )
            arguments = [
                "exec",
                helper,
                "-c",
                "capture",
                "--",
                "python",
                "-m",
                "sftpwarden.runtime.backup",
                "--applied",
                "/applied.json",
                "--container-info",
                "/container.json",
                "--topology",
                "kubernetes-runtime",
            ]
            if include_data:
                arguments.append("--include-data")
            with staged.open("xb") as stream:
                staged.chmod(0o600)
                result = subprocess.run(
                    kubectl_command(config, arguments, namespace=config.kubernetes.namespace),
                    stdout=stream,
                    stderr=subprocess.DEVNULL,
                    timeout=300,
                    check=False,
                )
            if result.returncode:
                raise RuntimeError("Kubernetes recovery capture failed; no backup was published.")
            manifest = verify_safety_archive(staged)
        finally:
            try:
                owned = get_resource(config, "pod", helper)
                if (
                    owned
                    and owned["metadata"].get("labels", {}).get("sftpwarden.io/capture-owner")
                    == nonce
                ):
                    request(config, ["delete", "pod", helper, "--wait=true", "--timeout=90s"])
            finally:
                if resume:
                    current = get_resource(config, "statefulset", name)
                    if (
                        current is None
                        or current["metadata"]["uid"] != workload["metadata"]["uid"]
                        or current["spec"]["template"] != workload["spec"]["template"]
                    ):
                        raise RuntimeError(
                            "Workload changed during capture; inspect maintenance "
                            "state before resuming."
                        )
                    if current["spec"].get("replicas") == 0:
                        request(
                            config,
                            [
                                "scale",
                                "statefulset",
                                name,
                                "--replicas=1",
                                "--current-replicas=0",
                                "--resource-version=" + current["metadata"]["resourceVersion"],
                            ],
                        )
                    elif current["spec"].get("replicas") != 1:
                        raise RuntimeError(
                            "Replica count changed during maintenance; inspect the "
                            "workload before resuming."
                        )
                    request(
                        config, ["wait", "--for=condition=Ready", f"pod/{name}-0", "--timeout=150s"]
                    )
                    replacement = get_resource(config, "pod", name + "-0")
                    if (
                        replacement is None
                        or _capture(config, workload, replacement)[1] != payload["fingerprints"]
                    ):
                        raise RuntimeError(
                            "Runtime identity verification failed after maintenance."
                        )
        with staged.open("rb") as source, atomic_binary_output(output) as target:
            shutil.copyfileobj(source, target)
    return BackupResult(path=output, entries=manifest.entries)
