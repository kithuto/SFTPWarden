"""Render Kubernetes manifests and Helm values for SFTPWarden projects."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import stat
from pathlib import Path
from typing import Any

import yaml

from sftpwarden.config import (
    EXTERNAL_DSN_PROVIDER_TYPES,
    FILE_PROVIDER_TYPES,
    KubernetesConfig,
    KubernetesProbeConfig,
    ProviderType,
    SFTPWardenConfig,
    provider_local_path,
)
from sftpwarden.providers import empty_provider_text
from sftpwarden.providers.documents import parse_provider_document
from sftpwarden.render.compose import runtime_image_reference
from sftpwarden.utils._version import get_version
from sftpwarden.utils.archives import MAX_METADATA_BYTES, check_managed_path
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.paths import expand_path

KUBERNETES_MANIFEST_FILE = "kubernetes.yml"
HELM_VALUES_FILE = "values.yaml"
PROVIDER_DSN_ENV = "SFTPWARDEN_PROVIDER_DSN"
PROVIDER_MOUNT = "/etc/sftpwarden/provider-data"
CONFIG_MAP_MOUNT = "/config"
HOST_KEY_SECRET_MOUNT = "/etc/sftpwarden/host_key_secret"
TEXT_SYNC_PROVIDER_TYPES = {ProviderType.YAML, ProviderType.CSV}


def kubernetes_resource_name(value: str) -> str:
    """Return a DNS-label-safe Kubernetes resource name."""
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    normalized = re.sub(r"-+", "-", normalized)
    return (normalized or "sftpwarden")[:63].rstrip("-")


def kubernetes_labels(config: SFTPWardenConfig) -> dict[str, str]:
    """Return common Kubernetes recommended labels."""
    release = kubernetes_resource_name(config.kubernetes.release)
    return {
        "app.kubernetes.io/name": "sftpwarden",
        "app.kubernetes.io/instance": release,
        "app.kubernetes.io/component": "runtime",
        "app.kubernetes.io/part-of": "sftpwarden",
        "app.kubernetes.io/managed-by": "sftpwarden",
    }


def kubernetes_runtime_config(config: SFTPWardenConfig) -> SFTPWardenConfig:
    """Return the runtime config rendered into the Kubernetes ConfigMap."""
    rendered = config.model_copy(deep=True)
    if rendered.provider.type in EXTERNAL_DSN_PROVIDER_TYPES and rendered.provider.dsn:
        rendered.provider.dsn = f"${{{PROVIDER_DSN_ENV}}}"
    if rendered.provider.type in FILE_PROVIDER_TYPES:
        rendered.provider.path = f"{PROVIDER_MOUNT}/{Path(rendered.provider.path).name}"
    return rendered


def kubernetes_config_text(config: SFTPWardenConfig) -> str:
    """Render the Kubernetes runtime config YAML."""
    from sftpwarden.config import dump_config

    return dump_config(kubernetes_runtime_config(config))


def kubernetes_manifests(
    config: SFTPWardenConfig, project_root: str | Path | None = None
) -> list[dict[str, Any]]:
    """Build Kubernetes resource manifests for SFTPWarden."""
    config.kubernetes.ensure_supported_replicas()
    name = kubernetes_resource_name(config.kubernetes.release)
    namespace = config.kubernetes.namespace
    labels = kubernetes_labels(config)
    config_map = _config_map(config, name, namespace, labels, project_root)
    resources = [config_map]
    resources.extend(_persistent_volume_claims(config, name, namespace, labels))
    resources.append(_service(config.kubernetes, name, namespace, labels, port=config.server.port))
    resources.append(_stateful_set(config, name, namespace, labels, config_map["data"]))
    return resources


def kubernetes_manifest_text(
    config: SFTPWardenConfig, project_root: str | Path | None = None
) -> str:
    """Render Kubernetes manifests as a YAML multi-document stream."""
    return (
        "# Offline render: live identity and credential prerequisites are unverified.\n"
        "# Run sftpwarden kube prepare before applying an upgrade; activate explicitly.\n"
        + yaml.safe_dump_all(kubernetes_manifests(config, project_root), sort_keys=False)
    )


def write_kubernetes_manifests(config: SFTPWardenConfig, project_root: str | Path = ".") -> Path:
    """Write rendered Kubernetes manifests into the project root."""
    root = expand_path(project_root)
    target = root / KUBERNETES_MANIFEST_FILE
    target.write_text(kubernetes_manifest_text(config, root), encoding="utf-8")
    return target


def helm_values_model(
    config: SFTPWardenConfig, project_root: str | Path | None = None
) -> dict[str, Any]:
    """Return starter Helm values derived from a project config."""
    repository, tag = split_image(runtime_image_reference(config, allow_local_build=False).image)
    runtime_config = kubernetes_runtime_config(config)
    values: dict[str, Any] = {
        "image": {
            "repository": repository,
            "tag": tag or get_version(),
            "pullPolicy": "IfNotPresent",
        },
        "runtime": {"replicas": config.kubernetes.replicas},
        "sftpwardenConfig": kubernetes_config_text(config),
        "service": {"type": config.kubernetes.service_type.value, "port": config.server.port},
        "kubernetes": {
            "namespace": config.kubernetes.namespace,
            "release": config.kubernetes.release,
            "kubeContext": config.kubernetes.kube_context,
            "storageClass": config.kubernetes.storage_class,
        },
        "provider": {
            "type": config.provider.type.value,
            "path": runtime_config.provider.path,
            "table": config.provider.table,
            "collection": config.provider.collection,
            "dsnSecretName": provider_dsn_secret_name(config),
            "dsnSecretKey": PROVIDER_DSN_ENV,
            "bootstrapContent": _provider_bootstrap_text(config, project_root),
        },
        "persistence": {
            "data": {"enabled": True, "size": config.kubernetes.data_storage_size},
            "state": {"enabled": True, "size": "1Gi"},
            "provider": {"enabled": config.provider.type in FILE_PROVIDER_TYPES, "size": "1Gi"},
        },
        "probes": {
            "startup": _helm_probe_values(config.kubernetes.startup_probe),
            "readiness": _helm_probe_values(config.kubernetes.readiness_probe),
            "liveness": _helm_probe_values(config.kubernetes.liveness_probe),
        },
        "hostKeys": {
            "secretName": config.kubernetes.host_keys_secret or "",
            "mountPath": HOST_KEY_SECRET_MOUNT,
        },
        "resources": {},
        "nodeSelector": {},
        "tolerations": [],
        "affinity": {},
    }
    if config.provider.type not in EXTERNAL_DSN_PROVIDER_TYPES:
        values["provider"]["dsnSecretName"] = None
    if config.provider.type not in FILE_PROVIDER_TYPES:
        values["provider"]["bootstrapContent"] = ""
    return values


def helm_values_text(config: SFTPWardenConfig, project_root: str | Path | None = None) -> str:
    """Render starter Helm values YAML."""
    return yaml.safe_dump(helm_values_model(config, project_root), sort_keys=False)


def write_helm_values(config: SFTPWardenConfig, project_root: str | Path = ".") -> Path:
    """Write starter Helm values into the project root."""
    root = expand_path(project_root)
    target = root / HELM_VALUES_FILE
    target.write_text(helm_values_text(config, root), encoding="utf-8")
    return target


def split_image(image: str) -> tuple[str, str | None]:
    """Split an image reference into repository and tag when a tag is present."""
    if ":" not in image.rsplit("/", 1)[-1]:
        return image, None
    repository, tag = image.rsplit(":", 1)
    return repository, tag


def _helm_probe_values(probe: KubernetesProbeConfig) -> dict[str, int]:
    """Serialize probe timing settings for Helm values."""
    return {
        "periodSeconds": probe.period_seconds,
        "timeoutSeconds": probe.timeout_seconds,
        "failureThreshold": probe.failure_threshold,
    }


def host_keys_secret_name(release: str) -> str:
    """Return the host-key Secret name for a release."""
    return f"{kubernetes_resource_name(release)}-host-keys"


def provider_dsn_secret_name(config: SFTPWardenConfig) -> str:
    """Return the provider DSN Secret name for a config."""
    return config.kubernetes.provider_dsn_secret or (
        f"{kubernetes_resource_name(config.kubernetes.release)}-provider"
    )


def _metadata(name: str, namespace: str, labels: dict[str, str]) -> dict[str, Any]:
    """Build shared Kubernetes object metadata."""
    return {"name": name, "namespace": namespace, "labels": labels}


def _config_map(
    config: SFTPWardenConfig,
    name: str,
    namespace: str,
    labels: dict[str, str],
    project_root: str | Path | None,
) -> dict[str, Any]:
    """Build the runtime configuration ConfigMap."""
    data = {"sftpwarden.yaml": kubernetes_config_text(config)}
    if _provider_syncs_from_config_map(config):
        data[_provider_runtime_filename(config)] = _provider_bootstrap_text(config, project_root)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _metadata(f"{name}-config", namespace, labels),
        "data": data,
    }


def _persistent_volume_claims(
    config: SFTPWardenConfig, name: str, namespace: str, labels: dict[str, str]
) -> list[dict[str, Any]]:
    """Build all persistent volume claims required by the runtime."""
    claims = [
        _pvc(
            f"{name}-data",
            namespace,
            labels,
            config.kubernetes.storage_class,
            config.kubernetes.data_storage_size,
        ),
        _pvc(f"{name}-state", namespace, labels, config.kubernetes.storage_class, "1Gi"),
    ]
    if config.provider.type in FILE_PROVIDER_TYPES:
        claims.append(
            _pvc(f"{name}-provider", namespace, labels, config.kubernetes.storage_class, "1Gi")
        )
    if not config.kubernetes.host_keys_secret:
        claims.append(
            _pvc(f"{name}-host-keys", namespace, labels, config.kubernetes.storage_class, "1Gi")
        )
    return claims


def _pvc(
    name: str,
    namespace: str,
    labels: dict[str, str],
    storage_class: str | None,
    size: str,
) -> dict[str, Any]:
    """Build one Kubernetes persistent volume claim."""
    spec: dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": size}},
    }
    if storage_class:
        spec["storageClassName"] = storage_class
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            **_metadata(name, namespace, labels),
            "annotations": {"helm.sh/resource-policy": "keep"},
        },
        "spec": spec,
    }


def _service(
    kubernetes: KubernetesConfig,
    name: str,
    namespace: str,
    labels: dict[str, str],
    *,
    port: int,
) -> dict[str, Any]:
    """Build the Kubernetes Service exposing SFTP."""
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _metadata(name, namespace, labels),
        "spec": {
            "type": kubernetes.service_type.value,
            "selector": labels,
            "ports": [{"name": "sftp", "port": port, "targetPort": "sftp"}],
        },
    }


def _kubernetes_probe(
    health_probe: dict[str, Any], probe: KubernetesProbeConfig, *, kind: str = "readiness"
) -> dict[str, Any]:
    """Combine a probe action with configured timing thresholds."""
    return {
        "exec": {"command": [*health_probe["exec"]["command"], "--probe", kind]},
        "failureThreshold": probe.failure_threshold,
        "periodSeconds": probe.period_seconds,
        "timeoutSeconds": probe.timeout_seconds,
    }


def _stateful_set(
    config: SFTPWardenConfig,
    name: str,
    namespace: str,
    labels: dict[str, str],
    config_data: dict[str, str],
) -> dict[str, Any]:
    """Build the complete SFTPWarden runtime StatefulSet."""
    volumes = [
        {"name": "config", "configMap": {"name": f"{name}-config"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": f"{name}-data"}},
        {"name": "state", "persistentVolumeClaim": {"claimName": f"{name}-state"}},
        {
            "name": "host-keys",
            **(
                {"emptyDir": {}}
                if config.kubernetes.host_keys_secret
                else {"persistentVolumeClaim": {"claimName": f"{name}-host-keys"}}
            ),
        },
    ]
    volume_mounts = [
        {
            "name": "config",
            "mountPath": CONTAINER_CONFIG_PATH,
            "subPath": "sftpwarden.yaml",
            "readOnly": True,
        },
        {"name": "data", "mountPath": config.server.data_dir},
        {"name": "state", "mountPath": config.server.state_dir},
        {"name": "host-keys", "mountPath": config.server.host_keys_dir},
    ]
    if config.provider.type in FILE_PROVIDER_TYPES:
        volumes.append(
            {"name": "provider", "persistentVolumeClaim": {"claimName": f"{name}-provider"}}
        )
        volume_mounts.append({"name": "provider", "mountPath": PROVIDER_MOUNT})
    init_volume_mounts = [
        {"name": "host-keys", "mountPath": "/host-keys"},
        {"name": "state", "mountPath": "/previous-state", "readOnly": True},
    ]
    init_command = (
        "python -m sftpwarden.runtime.host_keys --directory /host-keys --state /previous-state"
    )
    if config.kubernetes.host_keys_secret:
        volumes.append(
            {
                "name": "host-key-secret",
                "secret": {
                    "secretName": config.kubernetes.host_keys_secret,
                    "defaultMode": 0o600,
                },
            }
        )
        init_volume_mounts.append(
            {
                "name": "host-key-secret",
                "mountPath": "/host-key-secret",
                "readOnly": True,
            }
        )
        init_command += " --secret /host-key-secret"
    provider_bootstrap = _provider_bootstrap_command(config)
    if provider_bootstrap:
        init_volume_mounts.append({"name": "provider", "mountPath": PROVIDER_MOUNT})
        init_volume_mounts.append(
            {"name": "config", "mountPath": CONFIG_MAP_MOUNT, "readOnly": True}
        )
        init_command = f"{init_command}; {provider_bootstrap}"
    init_command = "set -eu; " + init_command
    image = runtime_image_reference(config, allow_local_build=False).image
    env: list[dict[str, Any]] = [{"name": "SFTPWARDEN_CONFIG", "value": CONTAINER_CONFIG_PATH}]
    if config.provider.type in EXTERNAL_DSN_PROVIDER_TYPES and config.provider.dsn:
        env.append(
            {
                "name": PROVIDER_DSN_ENV,
                "valueFrom": {
                    "secretKeyRef": {
                        "name": provider_dsn_secret_name(config),
                        "key": PROVIDER_DSN_ENV,
                    }
                },
            }
        )
    health_probe = {
        "exec": {
            "command": [
                "sftpwarden",
                "runtime",
                "health",
                "--config",
                CONTAINER_CONFIG_PATH,
            ]
        }
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": _metadata(name, namespace, labels),
        "spec": {
            "serviceName": name,
            "replicas": config.kubernetes.replicas,
            "updateStrategy": {"type": "OnDelete", "rollingUpdate": None},
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {
                    "labels": labels,
                    "annotations": {
                        "sftpwarden.io/config-revision": hashlib.sha256(
                            json.dumps(
                                config_data,
                                sort_keys=True,
                            ).encode()
                        ).hexdigest(),
                        "sftpwarden.io/activation": "explicit-identity-preflight-required",
                    },
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "initContainers": [
                        {
                            "name": "host-key-bootstrap",
                            "image": image,
                            "command": [
                                "sh",
                                "-c",
                                init_command,
                            ],
                            "volumeMounts": init_volume_mounts,
                        }
                    ],
                    "containers": [
                        {
                            "name": "sftpwarden",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "sftp", "containerPort": 22}],
                            "env": env,
                            "volumeMounts": volume_mounts,
                            "startupProbe": _kubernetes_probe(
                                health_probe, config.kubernetes.startup_probe, kind="startup"
                            ),
                            "readinessProbe": _kubernetes_probe(
                                health_probe, config.kubernetes.readiness_probe
                            ),
                            "livenessProbe": _kubernetes_probe(
                                health_probe, config.kubernetes.liveness_probe, kind="liveness"
                            ),
                            "securityContext": {
                                "privileged": False,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {
                                    "drop": ["ALL"],
                                    "add": [
                                        "CHOWN",
                                        "DAC_OVERRIDE",
                                        "FOWNER",
                                        "SETGID",
                                        "SETUID",
                                        "SYS_CHROOT",
                                    ],
                                },
                            },
                        }
                    ],
                    "volumes": volumes,
                },
            },
        },
    }


def _provider_bootstrap_text(
    config: SFTPWardenConfig, project_root: str | Path | None = None
) -> str:
    """Return validated initial content for a file provider."""
    if config.provider.type not in FILE_PROVIDER_TYPES:
        return ""
    if config.provider.type in TEXT_SYNC_PROVIDER_TYPES and project_root is not None:
        provider_path = provider_local_path(project_root, config)
        root = expand_path(project_root)
        try:
            check_managed_path(root, provider_path.relative_to(root).as_posix())
            before = provider_path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError
            with provider_path.open("rb") as source:
                content = source.read(MAX_METADATA_BYTES + 1)
            after = provider_path.lstat()
            if (
                len(content) > MAX_METADATA_BYTES
                or len(content) != before.st_size
                or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
                != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
            ):
                raise ValueError
            text = content.decode("utf-8")
        except (OSError, ValueError):
            raise ProviderError(
                "Local bootstrap provider is missing, unreadable or changing; no "
                "empty policy was rendered."
            ) from None
        users = parse_provider_document(
            text, "csv" if config.provider.type == ProviderType.CSV else "yaml"
        )
        if users.schema_version != config.provider.user_schema:
            raise ProviderError(
                "Bootstrap provider schema does not match the deployment configuration."
            )
        return text
    return empty_provider_text(config.provider.type, user_schema=config.provider.user_schema)


def _provider_bootstrap_command(config: SFTPWardenConfig) -> str | None:
    """Validate storage through the same Linux bootstrap used by the Helm chart."""
    if config.provider.type not in FILE_PROVIDER_TYPES:
        return None
    return shlex.join(
        [
            "python",
            "-m",
            "sftpwarden.runtime.bootstrap_provider",
            "--config",
            f"{CONFIG_MAP_MOUNT}/sftpwarden.yaml",
        ]
    )


def _provider_syncs_from_config_map(config: SFTPWardenConfig) -> bool:
    """Return whether the provider content is synced through a ConfigMap."""
    return config.provider.type in TEXT_SYNC_PROVIDER_TYPES


def _provider_runtime_filename(config: SFTPWardenConfig) -> str:
    """Return the provider filename used inside the runtime container."""
    return Path(kubernetes_runtime_config(config).provider.path).name
