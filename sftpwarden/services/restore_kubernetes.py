"""Restore into separately selected new cluster storage without replacing an old installation."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path, PurePosixPath
from uuid import uuid4

from sftpwarden.config import FILE_PROVIDER_TYPES, KubernetesMode, SFTPWardenConfig
from sftpwarden.contexts import ContextEntry
from sftpwarden.providers import provider_from_config
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.remote.deploy import remote_preparation_image
from sftpwarden.render.kubernetes import (
    kubernetes_manifests,
    kubernetes_resource_name,
    kubernetes_runtime_config,
)
from sftpwarden.runtime.host_keys import read_keys
from sftpwarden.services.backup_manifest import ValidatedBackup
from sftpwarden.services.kubernetes_identity import (
    decode_keys,
    get_resource,
    idle_helper_command,
    pin_context,
    request,
    write_resource,
)
from sftpwarden.services.kubernetes_lock import operation_lock
from sftpwarden.utils.errors import RuntimeError


def restore_new_cluster(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    archive: Path,
    source: Path,
    manifest: ValidatedBackup,
    *,
    include_data: bool,
    dry_run: bool,
) -> None:
    """Require matching local recovery policy and absent destination claims before allocation."""
    from sftpwarden.services.deploy import kubectl_command

    external = config.provider.type not in FILE_PROVIDER_TYPES
    if external:
        from sftpwarden.services.restore_external import protected_provider

        if protected_provider(entry, manifest) != config.provider:
            raise RuntimeError("The selected provider changed after separate recovery.")
    runtime = kubernetes_runtime_config(config)
    fields = ("server", "auth", "isolation", "uid_gid", "sync")
    for field in fields if external else ("provider", *fields):
        if getattr(runtime, field) != getattr(manifest.config, field):
            raise RuntimeError(
                "Cluster recovery configuration differs from the archive's runtime policy.",
                suggestion=(
                    "Restore the archive into a separate local --root, then select the "
                    "destination namespace/release before cluster recovery."
                ),
            )
    if require_configured_schema(provider_from_config(entry.root, config)) != manifest.users:
        raise RuntimeError(
            "Local recovery provider differs from the archive; activation would "
            "overwrite restored policy."
        )
    if not {"host_keys", "state"}.issubset(manifest.entries):
        raise RuntimeError("Cluster recovery requires a complete runtime archive.")
    config = pin_context(config)
    name = kubernetes_resource_name(config.kubernetes.release)
    resources = kubernetes_manifests(config, entry.root)
    claims = [item for item in resources if item["kind"] == "PersistentVolumeClaim"]

    def inspect() -> None:
        for kind, target in [
            ("statefulset", name),
            ("pod", name + "-0"),
            *[("pvc", item["metadata"]["name"]) for item in claims],
        ]:
            if get_resource(config, kind, target) is not None:
                raise RuntimeError(
                    "Cluster recovery requires a separate destination with no "
                    "existing workload or selected PVCs."
                )

    inspect()
    if config.kubernetes.host_keys_secret:
        secret = get_resource(config, "secret", config.kubernetes.host_keys_secret)
        keys = read_keys(source / "host_keys")
        data = {name: base64.b64encode(pair[0]).decode() for name, pair in keys.items()}
        if secret is None or decode_keys(secret.get("data", {}))[1] != decode_keys(data)[1]:
            raise RuntimeError(
                "The selected operator Secret does not match the recovery SSH identity."
            )
    if dry_run:
        return
    if get_resource(config, "namespace", config.kubernetes.namespace) is None:
        write_resource(
            config,
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": config.kubernetes.namespace},
            },
            action="create",
        )
    with operation_lock(config, "fresh-recovery"):
        inspect()
        nonce = uuid4().hex
        helper = f"{name[:35]}-restore-{nonce[:12]}"
        # Claims are deliberately retained on failure. The helper journals partial effects;
        # never turn failed restoration into automatic deletion of recovery material.
        for claim in claims:
            claim["metadata"].setdefault("labels", {})["sftpwarden.io/recovery-owner"] = nonce
            if config.kubernetes.mode == KubernetesMode.HELM:
                claim["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "Helm"
                claim["metadata"].setdefault("annotations", {}).update(
                    {
                        "meta.helm.sh/release-name": config.kubernetes.release,
                        "meta.helm.sh/release-namespace": config.kubernetes.namespace,
                        "helm.sh/resource-policy": "keep",
                    }
                )
            write_resource(config, claim, action="create")
        paths = {
            "data": runtime.server.data_dir,
            "state": runtime.server.state_dir,
            "host-keys": runtime.server.host_keys_dir,
        }
        if not external:
            paths["provider"] = str(PurePosixPath(runtime.provider.path).parent)
        volumes = [
            {
                "name": key,
                **(
                    {"emptyDir": {"medium": "Memory"}}
                    if key == "host-keys" and config.kubernetes.host_keys_secret
                    else {"persistentVolumeClaim": {"claimName": name + "-" + key}}
                ),
            }
            for key in paths
        ]
        try:
            write_resource(
                config,
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": helper,
                        "namespace": config.kubernetes.namespace,
                        "labels": {"sftpwarden.io/recovery-owner": nonce},
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "activeDeadlineSeconds": 600,
                        "containers": [
                            {
                                "name": "restore",
                                "image": remote_preparation_image(),
                                "command": idle_helper_command(),
                                "securityContext": {
                                    "runAsUser": 0,
                                    "allowPrivilegeEscalation": False,
                                },
                                "volumeMounts": [
                                    {"name": key, "mountPath": path} for key, path in paths.items()
                                ],
                            }
                        ],
                        "volumes": volumes,
                    },
                },
                action="create",
            )
            request(config, ["wait", "--for=condition=Ready", f"pod/{helper}", "--timeout=150s"])
            command = [
                "exec",
                "-i",
                helper,
                "-c",
                "restore",
                "--",
                "python",
                "-m",
                "sftpwarden.runtime.restore_cluster",
            ]
            if include_data:
                command.append("--include-data")
            with archive.open("rb") as stream:
                result = subprocess.run(
                    kubectl_command(config, command, namespace=config.kubernetes.namespace),
                    stdin=stream,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=300,
                    check=False,
                )
            if result.returncode:
                raise RuntimeError(
                    "Cluster file recovery failed; retain these PVCs and inspect the "
                    "private recovery marker before activation."
                )
        finally:
            owned = get_resource(config, "pod", helper)
            if (
                owned
                and owned["metadata"].get("labels", {}).get("sftpwarden.io/recovery-owner") == nonce
            ):
                request(config, ["delete", "pod", helper, "--wait=true", "--timeout=90s"])
