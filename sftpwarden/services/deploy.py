"""Deployment strategy planning and execution for Compose, Kubernetes, and Helm."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sftpwarden.config import DeployTarget, KubernetesMode, SFTPWardenConfig, load_config
from sftpwarden.contexts import ContextEntry, ContextType
from sftpwarden.remote.deploy import complete_compose_deployment, prepare_compose_deployment
from sftpwarden.remote.deploy import deploy_plan as compose_deploy_plan
from sftpwarden.remote.locking import operation_lock as remote_operation_lock
from sftpwarden.render.kubernetes import (
    HELM_VALUES_FILE,
    KUBERNETES_MANIFEST_FILE,
    helm_values_text,
    kubernetes_manifest_text,
    kubernetes_resource_name,
    write_helm_values,
    write_kubernetes_manifests,
)
from sftpwarden.services.context_cleanup import ensure_remote_only_root_available
from sftpwarden.services.provider_layout import provider_layout
from sftpwarden.system.commands import CommandResult, command_text, run
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import ContextError, RuntimeError

LOCAL_CHART_PATH = Path(__file__).resolve().parents[2] / "charts" / "sftpwarden"
CHART_PATH = str(LOCAL_CHART_PATH)
HELM_OCI_CHART_REF = "oci://ghcr.io/kithuto/charts/sftpwarden"


@dataclass(frozen=True)
class HelmChartReference:
    """Resolved Helm chart reference for local development or packaged installs."""

    reference: str
    version: str | None = None
    local: bool = False

    def command_args(self) -> list[str]:
        """Return Helm command arguments for this chart reference."""
        args = [self.reference]
        if self.version:
            args.extend(["--version", self.version])
        return args


class CommandRunner(Protocol):
    """Callable used to run external deployment commands."""

    def __call__(self, args: list[str], *, cwd: str | None = None) -> CommandResult:
        """Run a command and return its captured result."""
        ...


@dataclass(frozen=True)
class DeployAction:
    """One deploy action shown in dry-run output and JSON."""

    description: str
    command: list[str] | None = None
    resources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible action."""
        return {
            "description": self.description,
            "command": self.command,
            "resources": self.resources,
        }


@dataclass(frozen=True)
class DeploymentPlan:
    """Structured deployment plan for one context."""

    context: str
    target: str
    mode: str
    namespace: str | None
    release: str | None
    actions: list[DeployAction]

    def text(self) -> str:
        """Render a human-readable plan."""
        lines = [
            f"Context: {self.context}",
            f"Deploy target: {self.target}",
        ]
        if self.namespace:
            lines.append(f"Namespace: {self.namespace}")
        if self.release:
            lines.append(f"Release: {self.release}")
        lines.append("Actions:")
        for action in self.actions:
            lines.append(f"- {action.description}")
            if action.resources:
                lines.append(f"  resources: {', '.join(action.resources)}")
            if action.command:
                lines.append(f"  command: {command_text(action.command)}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible plan."""
        return {
            "context": self.context,
            "target": self.target,
            "mode": self.mode,
            "namespace": self.namespace,
            "release": self.release,
            "actions": [action.as_dict() for action in self.actions],
        }


def deployment_plan(context: ContextEntry) -> DeploymentPlan:
    """Build a deployment plan for the selected context."""
    if not context.config:
        return compose_deployment_plan(context)
    config = _context_config(context)
    if config.deploy.target == DeployTarget.KUBERNETES:
        if config.kubernetes.mode == KubernetesMode.HELM:
            return helm_deployment_plan(context, config)
        return kubernetes_deployment_plan(context, config)
    return compose_deployment_plan(context)


def apply_deployment_plan(context: ContextEntry, *, runner: CommandRunner = run) -> str:
    """Apply the selected context deployment plan."""
    if not context.config:
        if context.type == ContextType.LOCAL:
            raise ContextError("Local deployment requires a local sftpwarden.yaml configuration.")
        ensure_remote_only_root_available(context, runner=runner)
        plan = compose_deployment_plan(context)
        if not context.remote:
            raise ContextError("Remote deployment requires an explicit endpoint.")
        with remote_operation_lock(
            context.remote,
            runner=None if runner is run else lambda command: runner(command, cwd=None),
        ):
            _run_actions(plan, runner=runner, cwd=context.root)
        return f"Deployed {context.name} with Docker Compose."
    config = _context_config(context)
    if config.deploy.target == DeployTarget.KUBERNETES:
        return apply_kubernetes_deployment(context, config, runner=runner)
    lock = (
        remote_operation_lock(
            context.remote,
            create_root=True,
            runner=None if runner is run else lambda command: runner(command, cwd=None),
        )
        if context.type == ContextType.REMOTE and context.remote
        else nullcontext()
    )
    with lock:
        prepare_compose_deployment(context)
        plan = compose_deployment_plan(context)
        _run_actions(plan, runner=runner, cwd=context.root)
        complete_compose_deployment(context)
    return f"Deployed {context.name} with Docker Compose."


def apply_kubernetes_deployment(
    context: ContextEntry,
    config: SFTPWardenConfig,
    *,
    mode: KubernetesMode | None = None,
    install: bool = True,
    runner: CommandRunner = run,
) -> str:
    """Use the same private preparation and verified activation for every CLI entry point."""
    from sftpwarden.services.kubernetes_identity import get_resource, pin_context, write_resource
    from sftpwarden.services.kubernetes_lock import operation_lock

    selected = pin_context(config)
    if get_resource(selected, "namespace", selected.kubernetes.namespace) is None:
        write_resource(
            selected,
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": selected.kubernetes.namespace},
            },
            action="create",
        )
    with operation_lock(selected, "deployment"):
        return _apply_kubernetes_deployment(
            context, config, selected=selected, mode=mode, install=install, runner=runner
        )


def _apply_kubernetes_deployment(
    context: ContextEntry,
    config: SFTPWardenConfig,
    *,
    selected: SFTPWardenConfig,
    mode: KubernetesMode | None,
    install: bool,
    runner: CommandRunner,
) -> str:
    """Execute while holding the selected cluster's operation receipt."""
    from sftpwarden.services.kubernetes_identity import (
        get_resource,
        prepare_identity,
        prepare_provider_secret,
        wait_for_activation,
    )

    if not context.root:
        raise ContextError("Kubernetes deployment requires a local project root.")
    rendered_config = config
    config = config.model_copy(deep=True)
    config.kubernetes.mode = mode or config.kubernetes.mode
    config.kubernetes.kube_context = selected.kubernetes.kube_context
    name = kubernetes_resource_name(config.kubernetes.release)
    previous = get_resource(config, "pod", name + "-0")
    prepare_identity(config)
    prepare_provider_secret(config)
    if config.kubernetes.mode == KubernetesMode.HELM:
        plan = helm_deployment_plan(context, config)
        write_helm_values(rendered_config, context.root)
    else:
        plan = kubernetes_deployment_plan(context, config)
        write_kubernetes_manifests(rendered_config, context.root)
    for action in plan.actions:
        if action.command is None:
            continue
        command = list(action.command)
        if not install and command[0] == "helm" and "--install" in command:
            command.remove("--install")
        # Fresh installations already start the desired Pod; deleting it here
        # would interrupt initial provisioning for no operational benefit.
        if "delete" in command and previous is None:
            continue
        result = runner(command, cwd=context.root)
        if result.returncode:
            raise translate_command_failure(result)
    wait_for_activation(
        config,
        previous_uid=previous["metadata"]["uid"] if previous else None,
        expected_config=rendered_config,
    )
    return f"Deployed {context.name}; replacement runtime is ready."


def compose_deployment_plan(context: ContextEntry) -> DeploymentPlan:
    """Return the existing Docker Compose deployment plan as a structured plan."""
    legacy = compose_deploy_plan(context)
    actions = []
    if context.root and context.config:
        configured = _context_config(context)
        layout = provider_layout(Path(context.root), configured)
        if context.type == ContextType.REMOTE and configured.remote.include_env:
            actions.append(
                DeployAction(
                    description=(
                        "Privately deliver the explicitly selected .env to the remote project."
                    )
                )
            )
        if layout is not None and layout.needs_migration:
            actions.append(
                DeployAction(
                    description=(
                        "Migrate provider to a dedicated directory; preserve original data and "
                        "recovery config. Subsequent editors must use the new path."
                    ),
                    resources=[str(layout.source), str(layout.destination)],
                )
            )
        actions.append(
            DeployAction(description="Render Compose, recreate runtime and wait for readiness.")
        )
    return DeploymentPlan(
        context=context.name,
        target=DeployTarget.COMPOSE.value,
        mode="compose",
        namespace=None,
        release=None,
        actions=actions
        + [
            DeployAction(description="Run Docker Compose command", command=command)
            for command in legacy.commands
        ],
    )


def kubernetes_deployment_plan(
    context: ContextEntry, config: SFTPWardenConfig | None = None
) -> DeploymentPlan:
    """Build a kubectl manifests deployment plan."""
    loaded = config or _context_config(context)
    if context.type != ContextType.LOCAL and not context.root:
        raise ContextError(
            f"Context {context.name} has no local root.",
            suggestion="Use a local or remote local-sync context for Kubernetes deploys.",
        )
    command = kubectl_command(
        loaded,
        ["apply", "-f", KUBERNETES_MANIFEST_FILE],
        namespace=loaded.kubernetes.namespace,
    )
    restart_command = kubernetes_runtime_restart_command(loaded)
    runtime_statefulset = f"StatefulSet/{kubernetes_resource_name(loaded.kubernetes.release)}"
    return DeploymentPlan(
        context=context.name,
        target=DeployTarget.KUBERNETES.value,
        mode=KubernetesMode.MANIFESTS.value,
        namespace=loaded.kubernetes.namespace,
        release=loaded.kubernetes.release,
        actions=[
            DeployAction(
                description=(
                    "Privately preserve and verify server identity and provider "
                    "credentials before activation"
                )
            ),
            DeployAction(
                description="Render Kubernetes manifests",
                resources=kubernetes_resource_ids(loaded),
            ),
            DeployAction(
                description="Apply Kubernetes manifests with kubectl",
                command=command,
                resources=kubernetes_resource_ids(loaded),
            ),
            DeployAction(
                description="Replace the prepared runtime Pod to activate deployment changes",
                command=restart_command,
                resources=[runtime_statefulset],
            ),
            DeployAction(description="Wait for the replacement runtime to become ready"),
        ],
    )


def helm_deployment_plan(
    context: ContextEntry, config: SFTPWardenConfig | None = None
) -> DeploymentPlan:
    """Build a Helm deployment plan."""
    loaded = config or _context_config(context)
    chart = helm_chart_reference()
    command = helm_command(
        loaded,
        [
            "upgrade",
            "--install",
            loaded.kubernetes.release,
            *chart.command_args(),
            "--namespace",
            loaded.kubernetes.namespace,
            "--values",
            HELM_VALUES_FILE,
        ],
    )
    restart_command = kubernetes_runtime_restart_command(loaded)
    runtime_statefulset = f"StatefulSet/{kubernetes_resource_name(loaded.kubernetes.release)}"
    return DeploymentPlan(
        context=context.name,
        target=DeployTarget.KUBERNETES.value,
        mode=KubernetesMode.HELM.value,
        namespace=loaded.kubernetes.namespace,
        release=loaded.kubernetes.release,
        actions=[
            DeployAction(
                description=(
                    "Privately preserve and verify server identity and provider "
                    "credentials before activation"
                )
            ),
            DeployAction(description="Render Helm values", resources=["values.yaml"]),
            DeployAction(description="Upgrade or install Helm release", command=command),
            DeployAction(
                description="Replace the prepared runtime Pod to activate deployment changes",
                command=restart_command,
                resources=[runtime_statefulset],
            ),
            DeployAction(description="Wait for the replacement runtime to become ready"),
        ],
    )


def kubectl_command(
    config: SFTPWardenConfig,
    args: list[str],
    *,
    namespace: str | None = None,
) -> list[str]:
    """Build a kubectl command with configured context and namespace."""
    command = ["kubectl"]
    if config.kubernetes.kube_context:
        command.extend(["--context", config.kubernetes.kube_context])
    if namespace:
        command.extend(["-n", namespace])
    command.extend(args)
    return command


def kubernetes_runtime_restart_command(config: SFTPWardenConfig) -> list[str]:
    """Replace an OnDelete runtime only after identity preparation has succeeded."""
    return kubectl_command(
        config,
        [
            "delete",
            "pod",
            f"{kubernetes_resource_name(config.kubernetes.release)}-0",
            "--ignore-not-found",
            "--wait=true",
            "--timeout=120s",
        ],
        namespace=config.kubernetes.namespace,
    )


def helm_command(config: SFTPWardenConfig, args: list[str]) -> list[str]:
    """Build a helm command with configured kube context."""
    command = ["helm"]
    if config.kubernetes.kube_context:
        command.extend(["--kube-context", config.kubernetes.kube_context])
    command.extend(args)
    return command


def helm_chart_reference() -> HelmChartReference:
    """Resolve the Helm chart source for the current installation.

    Source checkouts use the local chart directory so development and tests can
    render unpublished changes. Installed Python packages fall back to the OCI
    chart published to GHCR with the same version as the installed CLI.
    """
    if LOCAL_CHART_PATH.exists():
        return HelmChartReference(reference=str(LOCAL_CHART_PATH), local=True)
    return HelmChartReference(reference=HELM_OCI_CHART_REF, version=get_version())


def kubernetes_resource_ids(config: SFTPWardenConfig) -> list[str]:
    """Return rendered Kubernetes resource identifiers."""
    resources = []
    for document in _safe_manifest_documents(config):
        resources.append(f"{document['kind']}/{document['metadata']['name']}")
    return resources


def kubernetes_rendered_manifest_diff_reason(config: SFTPWardenConfig, root: Path) -> str | None:
    """Return a deploy-change reason for Kubernetes manifests."""
    target = root / KUBERNETES_MANIFEST_FILE
    expected = kubernetes_manifest_text(config, root)
    if not target.exists():
        return f"{KUBERNETES_MANIFEST_FILE} is missing"
    if target.read_text(encoding="utf-8") != expected:
        return f"{KUBERNETES_MANIFEST_FILE} differs from current configuration"
    return None


def helm_values_diff_reason(config: SFTPWardenConfig, root: Path) -> str | None:
    """Return a deploy-change reason for Helm values."""
    target = root / HELM_VALUES_FILE
    expected = helm_values_text(config, root)
    if not target.exists():
        return f"{HELM_VALUES_FILE} is missing"
    if target.read_text(encoding="utf-8") != expected:
        return f"{HELM_VALUES_FILE} differs from current configuration"
    return None


def ensure_helm_values(config: SFTPWardenConfig, root: str | Path) -> Path:
    """Write Helm values for a project."""
    return write_helm_values(config, root)


def translate_command_failure(result: CommandResult) -> RuntimeError:
    """Translate common kubectl/helm failures into actionable errors."""
    output = result.output
    lower = output.lower()
    if result.returncode == 127:
        executable = result.args[0] if result.args else "command"
        return RuntimeError(
            f"Required executable not found: {executable}",
            suggestion=f"Install {executable} and try again.",
        )
    if _is_docker_compose_command(result.args):
        if "is not a docker command" in lower or "unknown command" in lower:
            return RuntimeError(
                "Docker Compose v2 is not available.",
                suggestion="Install Docker Compose v2 or enable the Docker Compose plugin.",
            )
        if (
            "cannot connect to the docker daemon" in lower
            or "docker daemon is not running" in lower
        ):
            return RuntimeError(
                "Docker daemon is not reachable.",
                suggestion="Start Docker Desktop or the Docker daemon, then retry.",
            )
        if "permission denied" in lower and "docker" in lower:
            return RuntimeError(
                "Docker daemon permissions are insufficient.",
                suggestion="Use an account with Docker access or fix Docker socket permissions.",
            )
        if any(text in lower for text in ("pull access denied", "manifest unknown")):
            return RuntimeError(
                "Docker image could not be pulled.",
                suggestion="Check the image name, tag, registry access, and release status.",
            )
    if "context" in lower and ("not found" in lower or "does not exist" in lower):
        return RuntimeError(
            "Kubernetes context was not found.",
            suggestion="Run `kubectl config get-contexts` or set kubernetes.kube_context.",
        )
    if "namespace" in lower and "not found" in lower:
        return RuntimeError(
            "Kubernetes namespace was not found.",
            suggestion="Create the namespace or update kubernetes.namespace.",
        )
    if "forbidden" in lower or "permission" in lower or "rbac" in lower:
        return RuntimeError(
            "Kubernetes RBAC permissions are insufficient.",
            suggestion="Grant the required namespace permissions and retry.",
        )
    if "storageclass" in lower and "not found" in lower:
        return RuntimeError(
            "Kubernetes storage class was not found.",
            suggestion="Set kubernetes.storage_class to an existing StorageClass.",
        )
    return RuntimeError(
        "Deployment command failed; private command output has been withheld.",
        suggestion=(
            "Inspect the selected deployment and its configuration privately before retrying."
        ),
    )


def _is_docker_compose_command(args: list[str]) -> bool:
    """Return whether arguments invoke Docker Compose."""
    return len(args) >= 2 and args[0] == "docker" and args[1] == "compose"


def _run_actions(
    plan: DeploymentPlan,
    *,
    runner: CommandRunner,
    cwd: str | None,
) -> None:
    """Execute all command-bearing deployment actions in order."""
    for action in plan.actions:
        if not action.command:
            continue
        result = runner(action.command, cwd=cwd)
        if result.returncode != 0:
            raise translate_command_failure(result)


def _context_config(context: ContextEntry) -> SFTPWardenConfig:
    """Load the required local configuration for a context."""
    if not context.config:
        raise ContextError(
            f"Context {context.name} has no local sftpwarden.yaml.",
            suggestion="Use a local or remote local-sync context.",
        )
    return load_config(context.config)


def _safe_manifest_documents(config: SFTPWardenConfig) -> list[dict[str, Any]]:
    """Build manifest documents through the validated renderer."""
    from sftpwarden.render.kubernetes import kubernetes_manifests

    return kubernetes_manifests(config)
