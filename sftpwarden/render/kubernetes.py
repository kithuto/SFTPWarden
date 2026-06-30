"""Render Kubernetes manifests and Helm values for SFTPWarden projects."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from sftpwarden.config import (
    EXTERNAL_DSN_PROVIDER_TYPES,
    FILE_PROVIDER_TYPES,
    KubernetesConfig,
    KubernetesProbeConfig,
    SFTPWardenConfig,
)
from sftpwarden.providers import empty_provider_text
from sftpwarden.render.compose import runtime_image_reference
from sftpwarden.utils._version import get_version
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.paths import expand_path

KUBERNETES_MANIFEST_FILE = "kubernetes.yml"
HELM_VALUES_FILE = "values.yaml"
PROVIDER_DSN_ENV = "SFTPWARDEN_PROVIDER_DSN"
PROVIDER_MOUNT = "/etc/sftpwarden/provider-data"
HOST_KEY_SECRET_MOUNT = "/etc/sftpwarden/host_key_secret"


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


def kubernetes_manifests(config: SFTPWardenConfig) -> list[dict[str, Any]]:
    """Build Kubernetes resource manifests for SFTPWarden."""
    config.kubernetes.ensure_supported_replicas()
    name = kubernetes_resource_name(config.kubernetes.release)
    namespace = config.kubernetes.namespace
    labels = kubernetes_labels(config)
    resources = [
        _config_map(config, name, namespace, labels),
        _host_keys_secret(name, namespace, labels),
    ]
    if config.provider.type in EXTERNAL_DSN_PROVIDER_TYPES and config.provider.dsn:
        resources.append(_provider_dsn_secret(config, name, namespace, labels))
    resources.extend(_persistent_volume_claims(config, name, namespace, labels))
    resources.append(_service(config.kubernetes, name, namespace, labels))
    resources.append(_stateful_set(config, name, namespace, labels))
    return resources


def kubernetes_manifest_text(config: SFTPWardenConfig) -> str:
    """Render Kubernetes manifests as a YAML multi-document stream."""
    return yaml.safe_dump_all(kubernetes_manifests(config), sort_keys=False)


def write_kubernetes_manifests(config: SFTPWardenConfig, project_root: str | Path = ".") -> Path:
    """Write rendered Kubernetes manifests into the project root."""
    target = expand_path(project_root) / KUBERNETES_MANIFEST_FILE
    target.write_text(kubernetes_manifest_text(config), encoding="utf-8")
    return target


def helm_values_model(config: SFTPWardenConfig) -> dict[str, Any]:
    """Return starter Helm values derived from a project config."""
    repository, tag = split_image(runtime_image_reference(config).image)
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
            "bootstrapContent": _provider_bootstrap_text(config),
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
            "secretName": host_keys_secret_name(config.kubernetes.release),
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


def helm_values_text(config: SFTPWardenConfig) -> str:
    """Render starter Helm values YAML."""
    return yaml.safe_dump(helm_values_model(config), sort_keys=False)


def write_helm_values(config: SFTPWardenConfig, project_root: str | Path = ".") -> Path:
    """Write starter Helm values into the project root."""
    target = expand_path(project_root) / HELM_VALUES_FILE
    target.write_text(helm_values_text(config), encoding="utf-8")
    return target


def split_image(image: str) -> tuple[str, str | None]:
    """Split an image reference into repository and tag when a tag is present."""
    if ":" not in image.rsplit("/", 1)[-1]:
        return image, None
    repository, tag = image.rsplit(":", 1)
    return repository, tag


def _helm_probe_values(probe: KubernetesProbeConfig) -> dict[str, int]:
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
    return f"{kubernetes_resource_name(config.kubernetes.release)}-provider"


def _metadata(name: str, namespace: str, labels: dict[str, str]) -> dict[str, Any]:
    return {"name": name, "namespace": namespace, "labels": labels}


def _config_map(
    config: SFTPWardenConfig, name: str, namespace: str, labels: dict[str, str]
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _metadata(f"{name}-config", namespace, labels),
        "data": {"sftpwarden.yaml": kubernetes_config_text(config)},
    }


def _host_keys_secret(name: str, namespace: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _metadata(f"{name}-host-keys", namespace, labels),
        "type": "Opaque",
        "stringData": {},
    }


def _provider_dsn_secret(
    config: SFTPWardenConfig, name: str, namespace: str, labels: dict[str, str]
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _metadata(f"{name}-provider", namespace, labels),
        "type": "Opaque",
        "stringData": {PROVIDER_DSN_ENV: config.provider.dsn or ""},
    }


def _persistent_volume_claims(
    config: SFTPWardenConfig, name: str, namespace: str, labels: dict[str, str]
) -> list[dict[str, Any]]:
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
    return claims


def _pvc(
    name: str,
    namespace: str,
    labels: dict[str, str],
    storage_class: str | None,
    size: str,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": size}},
    }
    if storage_class:
        spec["storageClassName"] = storage_class
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _metadata(name, namespace, labels),
        "spec": spec,
    }


def _service(
    kubernetes: KubernetesConfig, name: str, namespace: str, labels: dict[str, str]
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _metadata(name, namespace, labels),
        "spec": {
            "type": kubernetes.service_type.value,
            "selector": labels,
            "ports": [{"name": "sftp", "port": 22, "targetPort": "sftp"}],
        },
    }


def _kubernetes_probe(health_probe: dict[str, Any], probe: KubernetesProbeConfig) -> dict[str, Any]:
    return {
        **health_probe,
        "failureThreshold": probe.failure_threshold,
        "periodSeconds": probe.period_seconds,
        "timeoutSeconds": probe.timeout_seconds,
    }


def _stateful_set(
    config: SFTPWardenConfig, name: str, namespace: str, labels: dict[str, str]
) -> dict[str, Any]:
    volumes = [
        {"name": "config", "configMap": {"name": f"{name}-config"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": f"{name}-data"}},
        {"name": "state", "persistentVolumeClaim": {"claimName": f"{name}-state"}},
        {"name": "host-keys", "emptyDir": {}},
        {
            "name": "host-key-secret",
            "secret": {"secretName": f"{name}-host-keys", "optional": True},
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
        {"name": "host-key-secret", "mountPath": HOST_KEY_SECRET_MOUNT, "readOnly": True},
    ]
    if config.provider.type in FILE_PROVIDER_TYPES:
        volumes.append(
            {"name": "provider", "persistentVolumeClaim": {"claimName": f"{name}-provider"}}
        )
        volume_mounts.append({"name": "provider", "mountPath": PROVIDER_MOUNT})
    init_volume_mounts = [
        {"name": "host-keys", "mountPath": "/host-keys"},
        {
            "name": "host-key-secret",
            "mountPath": "/host-key-secret",
            "readOnly": True,
        },
    ]
    init_command = "cp /host-key-secret/* /host-keys/ 2>/dev/null || true; chmod 700 /host-keys"
    provider_bootstrap = _provider_bootstrap_command(config)
    if provider_bootstrap:
        init_volume_mounts.append({"name": "provider", "mountPath": PROVIDER_MOUNT})
        init_command = f"{init_command}; {provider_bootstrap}"
    image = runtime_image_reference(config).image
    env: list[dict[str, Any]] = [{"name": "SFTPWARDEN_CONFIG", "value": CONTAINER_CONFIG_PATH}]
    if config.provider.type in EXTERNAL_DSN_PROVIDER_TYPES and config.provider.dsn:
        env.append(
            {
                "name": PROVIDER_DSN_ENV,
                "valueFrom": {
                    "secretKeyRef": {
                        "name": f"{name}-provider",
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
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
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
                                health_probe, config.kubernetes.startup_probe
                            ),
                            "readinessProbe": _kubernetes_probe(
                                health_probe, config.kubernetes.readiness_probe
                            ),
                            "livenessProbe": _kubernetes_probe(
                                health_probe, config.kubernetes.liveness_probe
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


def _provider_bootstrap_text(config: SFTPWardenConfig) -> str:
    if config.provider.type not in FILE_PROVIDER_TYPES:
        return ""
    return empty_provider_text(config.provider.type)


def _provider_bootstrap_command(config: SFTPWardenConfig) -> str | None:
    if config.provider.type not in FILE_PROVIDER_TYPES:
        return None
    provider_path = kubernetes_runtime_config(config).provider.path
    bootstrap_text = _provider_bootstrap_text(config)
    quoted_path = shlex.quote(provider_path)
    quoted_dir = shlex.quote(str(Path(provider_path).parent))
    quoted_text = shlex.quote(bootstrap_text)
    return (
        f"mkdir -p {quoted_dir}; "
        f"test -f {quoted_path} || printf %s {quoted_text} > {quoted_path}; "
        f"chmod 600 {quoted_path}"
    )
