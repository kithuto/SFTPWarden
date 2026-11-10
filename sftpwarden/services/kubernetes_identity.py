"""Prepare durable Kubernetes identity before any workload replacement.

Private material travels through captured subprocess pipes, never command arguments,
rendered manifests or deployment output. Helpers are unique and removed in finally.
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
import uuid
from typing import Any

import yaml

from sftpwarden.config import (
    EXTERNAL_DSN_PROVIDER_TYPES,
    KubernetesMode,
    SFTPWardenConfig,
    runtime_config_fingerprints,
)
from sftpwarden.render.compose import runtime_image_reference
from sftpwarden.render.kubernetes import (
    PROVIDER_DSN_ENV,
    kubernetes_resource_name,
    kubernetes_runtime_config,
    provider_dsn_secret_name,
)
from sftpwarden.runtime.host_keys import KEY_TYPES, validated_key
from sftpwarden.security.environment import resolve_provider_dsn
from sftpwarden.utils.errors import RuntimeError


def idle_helper_command() -> list[str]:
    """Keep an exec helper alive while allowing PID 1 to exit cleanly on SIGTERM."""
    return [
        "python",
        "-c",
        "import signal, sys, time; "
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); time.sleep(600)",
    ]


def _prepare_update_strategy(config: SFTPWardenConfig, name: str, workload: dict[str, Any]) -> None:
    """Remove server-defaulted rolling settings without replacing a prepared Pod."""
    if workload["spec"].get("updateStrategy", {}).get("type") == "OnDelete":
        return
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": workload["metadata"]["uid"]},
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": workload["metadata"]["resourceVersion"],
        },
        {"op": "add", "path": "/spec/updateStrategy", "value": {"type": "OnDelete"}},
    ]
    request(config, ["patch", "statefulset", name, "--type=json", "-p", json.dumps(patch)])


def request(config: SFTPWardenConfig, arguments: list[str], payload: bytes | None = None) -> bytes:
    """Run a bounded, private kubectl operation with context and namespace fixed."""
    from sftpwarden.services.deploy import kubectl_command

    command = kubectl_command(config, arguments, namespace=config.kubernetes.namespace)
    return _private_command(command, payload)


def _private_command(command: list[str], payload: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(command, input=payload, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Kubernetes preparation failed or exceeded its deadline.") from None
    if result.returncode:
        raise RuntimeError(
            "Kubernetes preparation failed; private command output has been withheld.",
            suggestion=(
                "Check context, namespace, permissions, storage and Pod readiness before retrying."
            ),
        )
    return result.stdout


def pin_context(config: SFTPWardenConfig) -> SFTPWardenConfig:
    """Freeze the selected cluster before a multi-command mutation begins."""
    if config.kubernetes.kube_context:
        return config
    selected = _private_command(["kubectl", "config", "current-context"]).decode().strip()
    if not selected or any(character in selected for character in "\0\n\r"):
        raise RuntimeError("No unambiguous Kubernetes context is selected.")
    pinned = config.model_copy(deep=True)
    pinned.kubernetes.kube_context = selected
    return pinned


def ensure_release_retention(config: SFTPWardenConfig) -> None:
    """Check the recorded Helm manifest, not only live annotations, before uninstall."""
    from sftpwarden.services.deploy import helm_command

    content = _private_command(
        helm_command(
            config,
            [
                "get",
                "manifest",
                config.kubernetes.release,
                "--namespace",
                config.kubernetes.namespace,
            ],
        )
    )
    try:
        documents = [item for item in yaml.safe_load_all(content) if item]
    except yaml.YAMLError:
        raise RuntimeError("Installed Helm manifest could not be inspected safely.") from None
    retained = [
        item for item in documents if item.get("kind") in {"PersistentVolumeClaim", "Secret"}
    ]
    if not retained or any(
        item.get("metadata", {}).get("annotations", {}).get("helm.sh/resource-policy") != "keep"
        for item in retained
    ):
        raise RuntimeError(
            "Installed Helm release does not record storage retention. Complete the "
            "prepared upgrade before uninstalling."
        )
    prepare_identity(config)


def get_resource(config: SFTPWardenConfig, kind: str, name: str) -> dict[str, Any] | None:
    """Read one resource, distinguishing confirmed absence from inspection failure."""
    content = request(config, ["get", kind, name, "--ignore-not-found", "-o", "json"])
    if not content.strip():
        return None
    try:
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise RuntimeError("Kubernetes returned an invalid resource description.") from None


def write_resource(
    config: SFTPWardenConfig, document: dict[str, Any], *, action: str = "apply"
) -> None:
    """Send a resource privately through kubectl standard input."""
    request(config, [action, "-f", "-"], json.dumps(document).encode())


def decode_keys(data: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Validate every selected algorithm and return normalized material and public identity."""
    keys: dict[str, str] = {}
    fingerprints: dict[str, str] = {}
    try:
        if set(data) - {name + suffix for name in KEY_TYPES for suffix in ("", ".pub")}:
            raise ValueError
        for name in KEY_TYPES:
            if name not in data:
                if name + ".pub" in data:
                    raise ValueError
                continue
            private = base64.b64decode(data[name], validate=True)
            supplied = data.get(name + ".pub")
            public, fingerprint = validated_key(
                name,
                private,
                base64.b64decode(supplied, validate=True) if supplied else None,
            )
            keys[name] = base64.b64encode(private).decode()
            keys[name + ".pub"] = base64.b64encode(public).decode()
            fingerprints[name] = fingerprint
        if not fingerprints:
            raise ValueError
    except (ValueError, TypeError):
        raise RuntimeError(
            "Host-key source is empty, malformed or contains unsupported entries."
        ) from None
    return keys, fingerprints


# The source may still run 1.3.0: use only Python's standard library there.
_CAPTURE = """
import base64, json, pathlib, stat, sys
root = pathlib.Path(sys.argv[1])
result = {}
for name in sys.argv[2:]:
    path = root / name
    if not path.exists() and not path.is_symlink():
        continue
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
        raise SystemExit(1)
    result[name] = base64.b64encode(path.read_bytes()).decode()
print(json.dumps(result))
"""

_SEED = """
import base64, json, pathlib, sys
from sftpwarden.runtime.host_keys import (
    REQUIRE_EXISTING, prepare_host_keys, read_keys, validated_key,
)
from sftpwarden.utils.persistence import atomic_bytes
payload = json.load(sys.stdin)
root = pathlib.Path('/host-keys')
existing = read_keys(root)
current = {
    name: validated_key(name, private, public)[1]
    for name, (private, public) in existing.items()
}
if any(payload['fingerprints'].get(name) != fingerprint for name, fingerprint in current.items()):
    raise SystemExit(1)
atomic_bytes(root / REQUIRE_EXISTING, b'Preserved server identity; do not regenerate.\\n')
for name, content in payload['keys'].items():
    atomic_bytes(root / name, base64.b64decode(content, validate=True))
if prepare_host_keys(root) != payload['fingerprints']:
    raise SystemExit(1)
"""


def _capture(
    config: SFTPWardenConfig, workload: dict[str, Any], pod: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    containers = workload["spec"]["template"]["spec"]["containers"]
    runtime = next(item for item in containers if item["name"] == "sftpwarden")
    directory = next(
        item["mountPath"] for item in runtime["volumeMounts"] if item["name"] == "host-keys"
    )
    content = request(
        config,
        [
            "exec",
            pod["metadata"]["name"],
            "-c",
            "sftpwarden",
            "--",
            "python",
            "-c",
            _CAPTURE,
            directory,
            *[name + suffix for name in KEY_TYPES for suffix in ("", ".pub")],
        ],
    )
    try:
        return decode_keys(json.loads(content))
    except (ValueError, TypeError):
        raise RuntimeError(
            "The running server identity could not be captured; no rollout was started."
        ) from None


def _seed_volume(
    config: SFTPWardenConfig,
    pod: dict[str, Any],
    keys: dict[str, str],
    fingerprints: dict[str, str],
) -> None:
    name = kubernetes_resource_name(config.kubernetes.release)
    claim = name + "-host-keys"
    existing = get_resource(config, "pvc", claim)
    if existing is not None:
        labels = existing["metadata"].get("labels", {})
        annotations = existing["metadata"].get("annotations", {})
        ours = (
            labels.get("sftpwarden.io/identity-for") == name
            or (
                labels.get("app.kubernetes.io/managed-by") == "sftpwarden"
                and labels.get("app.kubernetes.io/instance") == name
            )
            or (
                annotations.get("meta.helm.sh/release-name") == config.kubernetes.release
                and annotations.get("meta.helm.sh/release-namespace") == config.kubernetes.namespace
            )
        )
        if not ours:
            raise RuntimeError(
                "Host-key PVC already exists without matching ownership; inspect and "
                "adopt it explicitly before migration."
            )
    if existing is None:
        metadata: dict[str, Any] = {
            "name": claim,
            "namespace": config.kubernetes.namespace,
            "labels": {"sftpwarden.io/identity-for": name},
        }
        if config.kubernetes.mode == KubernetesMode.HELM:
            metadata.update(
                {
                    "labels": {
                        "app.kubernetes.io/managed-by": "Helm",
                        "sftpwarden.io/identity-for": name,
                    },
                    "annotations": {
                        "meta.helm.sh/release-name": config.kubernetes.release,
                        "meta.helm.sh/release-namespace": config.kubernetes.namespace,
                        "helm.sh/resource-policy": "keep",
                    },
                }
            )
        spec: dict[str, Any] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "1Gi"}},
        }
        if config.kubernetes.storage_class:
            spec["storageClassName"] = config.kubernetes.storage_class
        write_resource(
            config,
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": metadata,
                "spec": spec,
            },
            action="create",
        )
    nonce = uuid.uuid4().hex
    helper = f"{name[:35]}-identity-{nonce[:12]}"
    specification: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        # Match the running node for RWO claims. Affinity allows delayed volume binding.
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
                "name": "prepare",
                "image": runtime_image_reference(config, allow_local_build=False).image,
                "command": idle_helper_command(),
                "volumeMounts": [{"name": "identity", "mountPath": "/host-keys"}],
                "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False},
            }
        ],
        "volumes": [{"name": "identity", "persistentVolumeClaim": {"claimName": claim}}],
    }
    try:
        write_resource(
            config,
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": helper,
                    "namespace": config.kubernetes.namespace,
                    "labels": {"sftpwarden.io/identity-helper": nonce},
                },
                "spec": specification,
            },
            action="create",
        )
        request(config, ["wait", "--for=condition=Ready", f"pod/{helper}", "--timeout=150s"])
        request(
            config,
            ["exec", "-i", helper, "-c", "prepare", "--", "python", "-c", _SEED],
            json.dumps({"keys": keys, "fingerprints": fingerprints}).encode(),
        )
    finally:
        try:
            # A create timeout may still have created the helper. Inspect the nonce
            # before deleting; never remove a same-named resource owned elsewhere.
            owned = get_resource(config, "pod", helper)
            if (
                owned
                and owned["metadata"].get("labels", {}).get("sftpwarden.io/identity-helper")
                == nonce
            ):
                request(
                    config,
                    ["delete", "pod", helper, "--ignore-not-found", "--wait=true", "--timeout=60s"],
                )
        except Exception:
            raise RuntimeError(
                f"Identity helper cleanup could not be confirmed: {helper}. "
                "Inspect this Pod before retrying; its PVC was retained."
            ) from None


def prepare_identity(config: SFTPWardenConfig) -> None:
    """Capture and verify the current identity before permitting explicit activation."""
    name = kubernetes_resource_name(config.kubernetes.release)
    workload = get_resource(config, "statefulset", name)
    secret_name = config.kubernetes.host_keys_secret
    secret = get_resource(config, "secret", secret_name) if secret_name else None
    secret_fingerprints = None
    if secret_name:
        if secret is None:
            raise RuntimeError("Configured host-key Secret is missing; no rollout was started.")
        _keys, secret_fingerprints = decode_keys(secret.get("data", {}))
    if workload is None:
        return
    if workload["spec"].get("replicas", 1) != 1:
        raise RuntimeError("Identity migration requires the existing single runtime to be running.")
    pod = get_resource(config, "pod", name + "-0")
    if pod is None or not any(
        owner.get("uid") == workload["metadata"]["uid"]
        for owner in pod["metadata"].get("ownerReferences", [])
    ):
        raise RuntimeError(
            "The existing runtime identity is unavailable; restore it before deploying."
        )
    keys, fingerprints = _capture(config, workload, pod)
    current_pod = get_resource(config, "pod", name + "-0")
    if current_pod is None or current_pod["metadata"]["uid"] != pod["metadata"]["uid"]:
        raise RuntimeError(
            "Runtime changed during identity capture; repeat preparation before deploying."
        )
    volumes = workload["spec"]["template"]["spec"].get("volumes", [])
    old_volume: dict[str, Any] = next((item for item in volumes if item["name"] == "host-keys"), {})
    legacy_secret = next(
        (
            item.get("secret", {}).get("secretName", "")
            for item in volumes
            if item["name"] == "host-key-secret"
        ),
        "",
    )
    if legacy_secret and "emptyDir" in old_volume:
        legacy = get_resource(config, "secret", legacy_secret)
        if legacy and legacy.get("data"):
            _material, legacy_fingerprints = decode_keys(legacy["data"])
            if legacy_fingerprints != fingerprints:
                raise RuntimeError(
                    "Legacy Secret conflicts with the running identity; resolve the "
                    "conflicting sources before deploying."
                )
    if not legacy_secret:
        previous_receipt = get_resource(config, "configmap", name + "-identity-prepared")
        if previous_receipt:
            legacy_secret = previous_receipt.get("data", {}).get("legacySecretName", "")
    if secret_name:
        if fingerprints != secret_fingerprints:
            raise RuntimeError(
                "Configured Secret differs from the running identity; unrelated "
                "deploys cannot rotate keys."
            )
    elif old_volume.get("persistentVolumeClaim", {}).get("claimName") != name + "-host-keys":
        _seed_volume(config, pod, keys, fingerprints)
    _prepare_update_strategy(config, name, workload)
    # Record only public evidence. Direct Helm upgrades check this receipt against
    # the workload UID and selected authority before changing legacy resources.
    write_resource(
        config,
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name + "-identity-prepared",
                "namespace": config.kubernetes.namespace,
            },
            "data": {
                "workloadUID": workload["metadata"]["uid"],
                "source": "secret:" + secret_name if secret_name else "pvc:" + name + "-host-keys",
                "secretVersion": str(secret["metadata"]["resourceVersion"]) if secret else "",
                "fingerprints": json.dumps(fingerprints, sort_keys=True),
                "legacySecretName": legacy_secret,
            },
        },
    )


def prepare_provider_secret(config: SFTPWardenConfig) -> None:
    """Resolve on the deploying host and privately deliver only the provider DSN."""
    if config.provider.type not in EXTERNAL_DSN_PROVIDER_TYPES:
        return
    name = provider_dsn_secret_name(config)
    existing = get_resource(config, "secret", name)
    if config.kubernetes.provider_dsn_secret:
        if existing is None or not existing.get("data", {}).get(PROVIDER_DSN_ENV):
            raise RuntimeError("Configured provider Secret or its DSN key is missing.")
        return
    marker = "sftpwarden.io/provider-secret"
    release = kubernetes_resource_name(config.kubernetes.release)
    if existing:
        labels = existing["metadata"].get("labels", {})
        managed = labels.get(marker) == release
        if (
            not managed
            and labels.get("app.kubernetes.io/managed-by") == "sftpwarden"
            and labels.get("app.kubernetes.io/instance") == release
        ):
            workload = get_resource(config, "statefulset", release)
            if workload:
                containers = workload["spec"]["template"]["spec"]["containers"]
                managed = any(
                    container.get("name") == "sftpwarden"
                    and any(
                        variable.get("name") == PROVIDER_DSN_ENV
                        and variable.get("valueFrom", {}).get("secretKeyRef", {}).get("name")
                        == name
                        for variable in container.get("env", [])
                    )
                    for container in containers
                )
        if not managed:
            raise RuntimeError(
                "Provider Secret already exists outside this deployment; select it "
                "explicitly or use another name."
            )
    if not config.provider.dsn:
        raise RuntimeError("Provider DSN is missing.")
    value = resolve_provider_dsn(config.provider.dsn)
    metadata: dict[str, Any] = {
        "name": name,
        "namespace": config.kubernetes.namespace,
        "labels": {marker: release},
    }
    if existing:
        metadata = {
            **existing["metadata"],
            "labels": {**existing["metadata"].get("labels", {}), marker: release},
        }
        if "annotations" in metadata:
            metadata["annotations"] = {
                key: value
                for key, value in metadata["annotations"].items()
                if key != "kubectl.kubernetes.io/last-applied-configuration"
            }
    write_resource(
        config,
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": metadata,
            "data": {PROVIDER_DSN_ENV: base64.b64encode(value.encode()).decode()},
        },
        action="replace" if existing else "create",
    )


def wait_for_activation(
    config: SFTPWardenConfig,
    *,
    previous_uid: str | None,
    expected_config: SFTPWardenConfig | None = None,
) -> None:
    """Observe a new ready Pod, never count the old Pod's readiness as success."""
    name = kubernetes_resource_name(config.kubernetes.release)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        pod = get_resource(config, "pod", name + "-0")
        if (
            pod
            and pod["metadata"].get("uid") != previous_uid
            and not pod["metadata"].get("deletionTimestamp")
        ):
            ready = any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in pod.get("status", {}).get("conditions", [])
            )
            if ready:
                workload = get_resource(config, "statefulset", name)
                if workload is None:
                    raise RuntimeError("Runtime workload disappeared during activation.")
                expected = runtime_config_fingerprints(
                    kubernetes_runtime_config(expected_config or config)
                )
                verification = (
                    "from sftpwarden.runtime.configuration import load_runtime_config; "
                    "from sftpwarden.config import runtime_config_fingerprints; "
                    "c=load_runtime_config('/etc/sftpwarden/sftpwarden.yaml'); "
                    f"assert runtime_config_fingerprints(c)=={expected!r}"
                )
                try:
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
                            verification,
                        ],
                    )
                except RuntimeError:
                    raise RuntimeError(
                        "Replacement runtime is ready but its applied configuration "
                        "could not be verified. Deployment is incomplete."
                    ) from None
                _keys, fingerprints = _capture(config, workload, pod)
                receipt = get_resource(config, "configmap", name + "-identity-prepared")
                if (
                    receipt
                    and receipt.get("data", {}).get("workloadUID") == workload["metadata"]["uid"]
                    and json.loads(receipt["data"]["fingerprints"]) != fingerprints
                ):
                    raise RuntimeError(
                        "Activated runtime identity differs from the preserved identity."
                    )
                return
        time.sleep(1)
    raise RuntimeError(
        "Deployment was applied, but the replacement runtime did not become ready "
        "before the deadline."
    )
