"""Prepare runtime artifacts before any provider or configuration migration."""

from __future__ import annotations

import shlex
import time
from uuid import uuid4

from packaging.version import Version

from sftpwarden.config import DeployTarget, KubernetesMode, SFTPWardenConfig
from sftpwarden.contexts import ContextEntry, ContextType
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.render.compose import runtime_image_reference
from sftpwarden.services.deployment_upgrade import UpgradePlan, context_config
from sftpwarden.services.image_probe import VERSION_PROBE, probe_version
from sftpwarden.system.commands import run
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.images import official_image


def preflight_runtime(entry: ContextEntry, plan: UpgradePlan) -> None:
    """Verify the candidate package in an isolated container, never the live server."""
    config = context_config(entry).model_copy(deep=True)
    if plan.kubernetes_mode is not None:
        config.deploy.target = DeployTarget.KUBERNETES
        config.kubernetes.mode = plan.kubernetes_mode
    if plan.kube_context:
        config.kubernetes.kube_context = plan.kube_context
    if official_image(config.docker.image):
        config.docker.image = f"ghcr.io/kithuto/sftpwarden:{plan.target}"
    reference = runtime_image_reference(
        config,
        allow_local_build=(
            entry.type == ContextType.LOCAL and config.deploy.target == DeployTarget.COMPOSE
        ),
    )
    if config.deploy.target == DeployTarget.KUBERNETES:
        if config.kubernetes.mode == KubernetesMode.HELM:
            import yaml

            from sftpwarden.services.deploy import helm_chart_reference

            chart = helm_chart_reference()
            result = run(["helm", "show", "chart", *chart.command_args()], timeout=60)
            if result.returncode:
                raise RuntimeError(
                    "The target Helm chart is unavailable; deployment was not changed."
                )
            metadata = yaml.safe_load(result.stdout)
            if (
                str(metadata.get("version")) != plan.target
                or str(metadata.get("appVersion")) != plan.target
            ):
                raise RuntimeError("The Helm chart does not match the selected runtime release.")
        applied = kubernetes_candidate(config, reference.image)
    else:

        def execute(command: list[str]) -> str:
            if entry.remote:
                command = [*ssh_base_command(entry.remote), shlex.join(command)]
            result = run(command, timeout=600)
            if result.returncode:
                raise RuntimeError(
                    "Candidate image preparation failed; deployment inputs were not changed."
                )
            return result.stdout.strip()

        if reference.build:
            execute(
                [
                    "docker",
                    "build",
                    "-t",
                    reference.image,
                    "-f",
                    str(reference.build["context"]) + "/" + reference.build["dockerfile"],
                    reference.build["context"],
                ]
            )
        else:
            execute(["docker", "pull", reference.image])
        applied = probe_version(execute, reference.image)
    try:
        version = Version(applied)
    except ValueError:
        raise RuntimeError("Candidate image returned an invalid package version.") from None
    if (plan.managed_image and version != Version(plan.target)) or version > Version(plan.target):
        raise RuntimeError("Candidate image does not match the supported target release.")
    if plan.source and version < Version(plan.source):
        raise RuntimeError("Candidate image would downgrade the runtime.")


def kubernetes_candidate(config: SFTPWardenConfig, image: str) -> str:
    """Check pullability/version in a disposable Pod with no project data mounts."""
    from sftpwarden.services.kubernetes_identity import get_resource, request, write_resource

    nonce = uuid4().hex
    name = f"sftpwarden-update-{nonce[:12]}"
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": config.kubernetes.namespace,
            "labels": {"sftpwarden.io/update-probe": nonce},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": VERSION_PROBE,
                    "securityContext": {
                        "readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    try:
        write_resource(config, pod, action="create")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = get_resource(config, "pod", name)
            phase = (current or {}).get("status", {}).get("phase")
            if phase == "Succeeded":
                return request(config, ["logs", name, "-c", "probe"]).decode().strip()
            if phase == "Failed":
                break
            time.sleep(0.5)
        raise RuntimeError(
            "Candidate image could not be verified; deployment inputs were not changed."
        )
    finally:
        current = get_resource(config, "pod", name)
        if (
            current
            and current["metadata"].get("labels", {}).get("sftpwarden.io/update-probe") == nonce
        ):
            request(config, ["delete", "pod", name, "--wait=true", "--timeout=30s"])
