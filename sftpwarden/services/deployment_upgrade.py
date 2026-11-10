"""Read-only deployed-version discovery and explicit, resumable upgrade records."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version

from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    SFTPWardenConfig,
    load_config,
    write_config,
)
from sftpwarden.contexts import ContextEntry, ContextType
from sftpwarden.remote.ssh import remote_path_expression, ssh_base_command
from sftpwarden.render.compose import DEFAULT_LOCAL_RUNTIME_IMAGE, GHCR_RUNTIME_IMAGE_REPOSITORY
from sftpwarden.render.kubernetes import kubernetes_resource_name
from sftpwarden.services.image_probe import VERSION_PROBE, probe_version
from sftpwarden.services.kubernetes_identity import pin_context
from sftpwarden.services.releases import stable_version
from sftpwarden.system.commands import run
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.images import official_image
from sftpwarden.utils.paths import app_home
from sftpwarden.utils.persistence import atomic_bytes, reject_symlink
from sftpwarden.utils.state_transactions import state_transaction

BACKUP_ADVICE = (
    "A verified backup of provider, data, state and host keys is recommended before deploy. "
    "Legacy 1.3.0 needs the runbook's stopped-maintenance capture; "
    "a project-only backup is insufficient."
)


def request(command: list[str], *, cwd: str | None = None) -> str:
    """Run a bounded private version probe against the selected deployment."""
    result = run(command, cwd=cwd, timeout=45)
    if result.returncode:
        raise RuntimeError(
            "Deployed version could not be inspected; no upgrade was started.",
            suggestion="Check runtime access. An unavailable target is not a fresh installation.",
        )
    return result.stdout.strip()


def deployed_version(entry: ContextEntry, *, config: SFTPWardenConfig | None = None) -> str | None:
    """Inspect the actual selected runtime, never infer its release from desired config."""
    config = config or (load_config(entry.config) if entry.config else None)
    if config and config.deploy.target == DeployTarget.KUBERNETES:
        from sftpwarden.services.deploy import kubectl_command

        name = kubernetes_resource_name(config.kubernetes.release)
        namespace = config.kubernetes.namespace
        workload = request(
            kubectl_command(
                config,
                ["get", "statefulset", name, "--ignore-not-found", "-o", "name"],
                namespace=namespace,
            )
        )
        if not workload:
            return None
        value = request(
            kubectl_command(
                config,
                ["exec", name + "-0", "-c", "sftpwarden", "--", *VERSION_PROBE],
                namespace=namespace,
            )
        )
    else:
        remote = entry.remote if entry.type == ContextType.REMOTE else None
        filename = (
            remote.compose_file
            if remote
            else config.docker.compose_file
            if config
            else "docker-compose.yml"
        )
        compose = ["docker", "compose", "-f", filename]
        if remote:
            prefix = f"cd {remote_path_expression(remote.remote_root)} && "
            probe = (
                f"if test -d {remote_path_expression(remote.remote_root)}; then "
                + prefix
                + f"if test -f {shlex.quote(filename)}; then "
                + shlex.join([*compose, "ps", "--all", "--quiet", "sftpwarden"])
                + "; fi; fi"
            )
            container = request([*ssh_base_command(remote), probe])
            command = [
                *ssh_base_command(remote),
                prefix + shlex.join([*compose, "exec", "-T", "sftpwarden", *VERSION_PROBE]),
            ]
        else:
            if not (Path(entry.root) / filename).exists():
                return None
            container = request([*compose, "ps", "--all", "--quiet", "sftpwarden"], cwd=entry.root)
            command = [*compose, "exec", "-T", "sftpwarden", *VERSION_PROBE]
        if not container:
            return None
        if len(container.splitlines()) != 1:
            raise RuntimeError("Multiple runtimes matched the selected deployment.")
        try:
            value = request(command, cwd=None if remote else entry.root)
        except RuntimeError:
            # A stopped server cannot exec. Inspect its immutable image ID and
            # run only the metadata probe, without its data or network mounts.
            def on_engine(arguments: list[str]) -> str:
                if remote:
                    return request([*ssh_base_command(remote), shlex.join(arguments)])
                return request(arguments, cwd=entry.root)

            inspection = json.loads(on_engine(["docker", "inspect", container]))
            if len(inspection) != 1 or inspection[0]["State"]["Running"]:
                raise
            image_id = inspection[0]["Image"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise RuntimeError(
                    "The stopped runtime image identity could not be verified."
                ) from None
            value = probe_version(on_engine, image_id)
    try:
        return str(stable_version(value))
    except Exception:
        raise RuntimeError("Runtime returned an unsupported or invalid version.") from None


@dataclass(frozen=True)
class UpgradePlan:
    """Detected deployment transition, separate from the package installation."""

    source: str | None
    target: str
    kind: str
    managed_image: bool
    backup_advice: str
    kube_context: str | None = None
    config_fingerprint: str = ""
    kubernetes_mode: KubernetesMode | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return additive public deployment-plan metadata."""
        result = asdict(self)
        result.pop("config_fingerprint")
        return result


def context_config(entry: ContextEntry) -> SFTPWardenConfig:
    """Inspect remote-only image intent without exposing private configuration."""
    if entry.config:
        return load_config(entry.config)
    if entry.remote:
        content = request(
            [
                *ssh_base_command(entry.remote),
                "head -c 1048577 " + remote_path_expression(entry.remote.remote_config),
            ]
        )
        try:
            if len(content.encode()) > 1048576:
                raise ValueError("Oversized configuration")
            return SFTPWardenConfig.model_validate(yaml.safe_load(content))
        except Exception:
            raise RuntimeError(
                "The remote deployment configuration could not be validated."
            ) from None
    raise RuntimeError("The deployment has no configuration to inspect.")


def plan_upgrade(
    entry: ContextEntry,
    *,
    target: str | None = None,
    kubernetes_mode: KubernetesMode | None = None,
) -> UpgradePlan:
    """Reject unknown transition chains and downgrades before mutation."""
    config = context_config(entry)
    fingerprint = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
    if kubernetes_mode is not None:
        config.deploy.target = DeployTarget.KUBERNETES
        config.kubernetes.mode = kubernetes_mode
    if config.deploy.target == DeployTarget.KUBERNETES:
        config = pin_context(config)
    source = deployed_version(entry, config=config)
    target = target or get_version()
    if source and Version(source) > Version(target):
        raise RuntimeError("Deploy would downgrade a newer runtime; use a compatible CLI instead.")
    if source and Version(source) < Version("1.3.0"):
        raise RuntimeError(
            "Automatic migration from this release is not verified; upgrade through 1.3.0 first."
        )
    managed = config.docker.image == DEFAULT_LOCAL_RUNTIME_IMAGE or official_image(
        config.docker.image
    )
    kind = (
        "install"
        if source is None
        else "upgrade"
        if Version(source) < Version(target)
        else "redeploy"
    )
    return UpgradePlan(
        source,
        target,
        kind,
        managed,
        BACKUP_ADVICE,
        config.kubernetes.kube_context if config.deploy.target == DeployTarget.KUBERNETES else None,
        fingerprint,
        kubernetes_mode,
    )


def receipt_path(
    entry: ContextEntry,
    *,
    kube_context: str | None = None,
    kubernetes_mode: KubernetesMode | None = None,
) -> Path:
    """Bind upgrade evidence to the complete deployment target identity."""
    identity = {
        "name": entry.name,
        "root": entry.root,
        "config": entry.config,
        "remote": entry.remote.model_dump(mode="json", exclude={"ssh_key"})
        if entry.remote
        else None,
    }
    if entry.config:
        config = load_config(entry.config)
        if kubernetes_mode is not None:
            config.deploy.target = DeployTarget.KUBERNETES
            config.kubernetes.mode = kubernetes_mode
        identity["target"] = {
            "deploy": config.deploy.model_dump(mode="json"),
            "cluster": kube_context or config.kubernetes.kube_context,
            "namespace": config.kubernetes.namespace,
            "release": config.kubernetes.release,
        }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return app_home() / "updates" / "deployments" / f"{digest}.json"


def begin_upgrade(entry: ContextEntry, plan: UpgradePlan) -> None:
    """Record pending effects and advance only official desired image references."""
    current = context_config(entry)
    if hashlib.sha256(current.model_dump_json().encode()).hexdigest() != plan.config_fingerprint:
        raise RuntimeError("Deployment configuration changed after confirmation; repeat deploy.")
    if plan.kind == "upgrade":
        from sftpwarden.services.update_artifacts import preflight_runtime

        preflight_runtime(entry, plan)
    path = receipt_path(entry, kube_context=plan.kube_context, kubernetes_mode=plan.kubernetes_mode)
    reject_symlink(path)
    atomic_bytes(path, json.dumps({"status": "pending", **plan.as_dict()}).encode())
    if entry.config:
        with state_transaction(Path(entry.config)):
            config = load_config(entry.config)
            previous = config.model_dump_json()
            if official_image(config.docker.image):
                config.docker.image = f"{GHCR_RUNTIME_IMAGE_REPOSITORY}:{plan.target}"
            if plan.kube_context:
                config.kubernetes.kube_context = plan.kube_context
            if config.model_dump_json() != previous:
                write_config(entry.config, config)


def complete_upgrade(
    entry: ContextEntry,
    plan: UpgradePlan,
    *,
    reconcile_watcher: bool = True,
) -> dict[str, Any]:
    """Verify applied runtime and watcher effects before publishing completion."""
    from sftpwarden.watcher.upgrade import upgrade_watcher

    config = context_config(entry)
    if plan.kubernetes_mode is not None:
        config.deploy.target = DeployTarget.KUBERNETES
        config.kubernetes.mode = plan.kubernetes_mode
    if plan.kube_context:
        config.kubernetes.kube_context = plan.kube_context
    applied = deployed_version(entry, config=config)
    if applied is None or (plan.managed_image and Version(applied) != Version(plan.target)):
        raise RuntimeError("Runtime version verification failed; the upgrade remains incomplete.")
    if Version(applied) > Version(plan.target) or (
        plan.source and Version(applied) < Version(plan.source)
    ):
        raise RuntimeError("The custom runtime version is incompatible with this upgrade.")
    watcher = upgrade_watcher() if reconcile_watcher else {"status": "pending_coordinated_upgrade"}
    result = {
        "status": "complete" if reconcile_watcher else "runtime_ready",
        **plan.as_dict(),
        "applied": applied,
        "watcher": watcher,
    }
    if not plan.managed_image and applied != plan.target:
        result["status"] = "custom_image_preserved"
        result["action"] = "Update the custom image explicitly to adopt the new runtime release."
    atomic_bytes(
        receipt_path(entry, kube_context=plan.kube_context, kubernetes_mode=plan.kubernetes_mode),
        json.dumps(result).encode(),
    )
    return result
