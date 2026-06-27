"""Deployment strategy planning and execution for Compose, Kubernetes, and Helm."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sftpwarden.config import DeployTarget, KubernetesMode, SFTPWardenConfig, load_config
from sftpwarden.contexts import ContextEntry, ContextType
from sftpwarden.remote.deploy import deploy_plan as compose_deploy_plan
from sftpwarden.render.kubernetes import (
    HELM_VALUES_FILE,
    KUBERNETES_MANIFEST_FILE,
    helm_values_text,
    kubernetes_manifest_text,
    write_helm_values,
    write_kubernetes_manifests,
)
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
        plan = compose_deployment_plan(context)
        _run_actions(plan, runner=runner, cwd=context.root)
        return f"Deployed {context.name} with Docker Compose."
    config = _context_config(context)
    if config.deploy.target == DeployTarget.KUBERNETES:
        if config.kubernetes.mode == KubernetesMode.HELM:
            plan = helm_deployment_plan(context, config)
            if context.root:
                write_helm_values(config, context.root)
            _run_actions(plan, runner=runner, cwd=context.root)
            return f"Deployed {context.name} with Helm."
        plan = kubernetes_deployment_plan(context, config)
        if not context.root:
            raise ContextError(
                f"Context {context.name} has no local root.",
                suggestion="Use a local or remote local-sync context for Kubernetes deploys.",
            )
        write_kubernetes_manifests(config, context.root)
        _run_actions(plan, runner=runner, cwd=context.root)
        return f"Applied Kubernetes manifests for {context.name}."
    plan = compose_deployment_plan(context)
    _run_actions(plan, runner=runner, cwd=context.root)
    return f"Deployed {context.name} with Docker Compose."


def compose_deployment_plan(context: ContextEntry) -> DeploymentPlan:
    """Return the existing Docker Compose deployment plan as a structured plan."""
    legacy = compose_deploy_plan(context)
    return DeploymentPlan(
        context=context.name,
        target=DeployTarget.COMPOSE.value,
        mode="compose",
        namespace=None,
        release=None,
        actions=[
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
    return DeploymentPlan(
        context=context.name,
        target=DeployTarget.KUBERNETES.value,
        mode=KubernetesMode.MANIFESTS.value,
        namespace=loaded.kubernetes.namespace,
        release=loaded.kubernetes.release,
        actions=[
            DeployAction(
                description="Render Kubernetes manifests",
                resources=kubernetes_resource_ids(loaded),
            ),
            DeployAction(
                description="Apply Kubernetes manifests with kubectl",
                command=command,
                resources=kubernetes_resource_ids(loaded),
            ),
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
    return DeploymentPlan(
        context=context.name,
        target=DeployTarget.KUBERNETES.value,
        mode=KubernetesMode.HELM.value,
        namespace=loaded.kubernetes.namespace,
        release=loaded.kubernetes.release,
        actions=[
            DeployAction(description="Render Helm values", resources=["values.yaml"]),
            DeployAction(description="Upgrade or install Helm release", command=command),
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
    expected = kubernetes_manifest_text(config)
    if not target.exists():
        return f"{KUBERNETES_MANIFEST_FILE} is missing"
    if target.read_text(encoding="utf-8") != expected:
        return f"{KUBERNETES_MANIFEST_FILE} differs from current configuration"
    return None


def helm_values_diff_reason(config: SFTPWardenConfig, root: Path) -> str | None:
    """Return a deploy-change reason for Helm values."""
    target = root / HELM_VALUES_FILE
    expected = helm_values_text(config)
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
        f"Deployment command failed: {command_text(result.args)}",
        suggestion=output or "Inspect kubectl or helm output and retry.",
    )


def _is_docker_compose_command(args: list[str]) -> bool:
    return len(args) >= 2 and args[0] == "docker" and args[1] == "compose"


def _run_actions(
    plan: DeploymentPlan,
    *,
    runner: CommandRunner,
    cwd: str | None,
) -> None:
    for action in plan.actions:
        if not action.command:
            continue
        result = runner(action.command, cwd=cwd)
        if result.returncode != 0:
            raise translate_command_failure(result)


def _context_config(context: ContextEntry) -> SFTPWardenConfig:
    if not context.config:
        raise ContextError(
            f"Context {context.name} has no local sftpwarden.yaml.",
            suggestion="Use a local or remote local-sync context.",
        )
    return load_config(context.config)


def _safe_manifest_documents(config: SFTPWardenConfig) -> list[dict[str, Any]]:
    from sftpwarden.render.kubernetes import kubernetes_manifests

    return kubernetes_manifests(config)
