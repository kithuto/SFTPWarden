"""Kubernetes, Helm, and deployment strategy behavior tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

import sftpwarden.cli_commands.core as core_commands
import sftpwarden.cli_commands.helm as helm_commands
import sftpwarden.cli_commands.init as init_commands
import sftpwarden.cli_commands.kubernetes as kube_commands
import sftpwarden.render.compose as compose_module
import sftpwarden.services.cli_workflows as cli_workflows
import sftpwarden.services.deploy as deploy_module
from sftpwarden.cli import app
from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    ProviderType,
    SFTPWardenConfig,
    default_project_config,
    load_config,
    validation_error_to_config_error,
    write_config,
)
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    ContextType,
    local_context,
    save_registry,
)
from sftpwarden.remote.deploy import DeployPlan
from sftpwarden.remote.deploy import deploy_context as legacy_deploy_context
from sftpwarden.render.kubernetes import (
    PROVIDER_DSN_ENV,
    helm_values_model,
    kubernetes_manifest_text,
    kubernetes_manifests,
    split_image,
)
from sftpwarden.services.deploy import (
    HELM_OCI_CHART_REF,
    DeployAction,
    DeploymentPlan,
    HelmChartReference,
    apply_deployment_plan,
    deployment_plan,
    ensure_helm_values,
    helm_chart_reference,
    helm_command,
    helm_values_diff_reason,
    kubectl_command,
    kubernetes_deployment_plan,
    kubernetes_rendered_manifest_diff_reason,
    translate_command_failure,
)
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import ContextError, ProviderError, RuntimeError, SFTPWardenError


def register_kubernetes_project(
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
    tmp_path: Path,
    name: str,
    mode: KubernetesMode,
) -> Path:
    """Create a registered Kubernetes project without exercising the init CLI."""
    root, _entry = local_project_factory(name=name, root=tmp_path / name)
    config = load_config(root / "sftpwarden.yaml")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = mode
    write_config(root / "sftpwarden.yaml", config)
    compose_file = root / config.docker.compose_file
    if compose_file.exists():
        compose_file.unlink()
    return root


@pytest.fixture(autouse=True)
def fake_init_kubernetes_namespace_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Kubernetes init tests independent from a live cluster by default."""

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        return CommandResult(command, 0, "ok\n", "")

    monkeypatch.setattr(init_commands, "run", fake_run)


def test_kubernetes_replicas_are_reserved_for_future_multi_node() -> None:
    """Reject Kubernetes replica counts reserved for future multi-node support."""
    with pytest.raises(ValidationError, match="Kubernetes replicas > 1"):
        SFTPWardenConfig.model_validate({"project": {"name": "dev"}, "kubernetes": {"replicas": 2}})


def test_kubernetes_manifest_rendering_includes_core_resources() -> None:
    """Render the Kubernetes resources required by the v1.3 operating model."""
    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = KubernetesMode.MANIFESTS
    config.kubernetes.namespace = "sftp"
    config.kubernetes.release = "sftp-prod"
    config.kubernetes.data_storage_size = "50Gi"
    config.kubernetes.startup_probe.timeout_seconds = 7
    config.kubernetes.startup_probe.failure_threshold = 12
    config.kubernetes.readiness_probe.period_seconds = 15
    config.kubernetes.liveness_probe.period_seconds = 45

    manifests = kubernetes_manifests(config)
    rendered = kubernetes_manifest_text(config)
    kinds = {manifest["kind"] for manifest in manifests}
    statefulset = next(manifest for manifest in manifests if manifest["kind"] == "StatefulSet")
    data_pvc = next(
        manifest
        for manifest in manifests
        if manifest["kind"] == "PersistentVolumeClaim"
        and manifest["metadata"]["name"] == "sftp-prod-data"
    )

    assert {"ConfigMap", "Secret", "PersistentVolumeClaim", "Service", "StatefulSet"} <= kinds
    assert statefulset["spec"]["replicas"] == 1
    assert data_pvc["spec"]["resources"]["requests"]["storage"] == "50Gi"
    container = statefulset["spec"]["template"]["spec"]["containers"][0]
    probe_command = container["startupProbe"]["exec"]["command"]
    init_container = statefulset["spec"]["template"]["spec"]["initContainers"][0]
    assert probe_command[:3] == ["sftpwarden", "runtime", "health"]
    assert container["startupProbe"]["timeoutSeconds"] == 7
    assert container["startupProbe"]["failureThreshold"] == 12
    assert container["readinessProbe"]["periodSeconds"] == 15
    assert container["readinessProbe"]["failureThreshold"] == 3
    assert container["livenessProbe"]["periodSeconds"] == 45
    assert container["livenessProbe"]["failureThreshold"] == 3
    assert "provider-data/users.yaml" in init_container["command"][-1]
    assert any(mount["name"] == "provider" for mount in init_container["volumeMounts"])
    assert "sftp-prod-data" in rendered
    assert "sftp-prod-state" in rendered
    assert "sftp-prod-host-keys" in rendered


def test_kubernetes_manifest_keeps_external_dsn_in_secret() -> None:
    """Move external provider DSNs out of ConfigMaps and into Secrets."""
    config = default_project_config(
        "prod",
        ProviderType.POSTGRESQL,
        dsn="postgresql://user:pass@db/sftp",
    )
    config.deploy.target = DeployTarget.KUBERNETES
    manifests = kubernetes_manifests(config)
    configmap = next(manifest for manifest in manifests if manifest["kind"] == "ConfigMap")
    secrets = [manifest for manifest in manifests if manifest["kind"] == "Secret"]
    rendered_config = configmap["data"]["sftpwarden.yaml"]

    assert "postgresql://user:pass@db/sftp" not in rendered_config
    assert f"${{{PROVIDER_DSN_ENV}}}" in rendered_config
    assert any(secret.get("stringData", {}).get(PROVIDER_DSN_ENV) for secret in secrets)


@pytest.mark.parametrize(
    ("provider_type", "filename", "provider_text"),
    [
        (
            ProviderType.YAML,
            "users.yaml",
            "users:\n- username: alice\n  password_hash: '!'\n",
        ),
        (
            ProviderType.CSV,
            "users.csv",
            (
                "username,public_keys,password_hash,uid,gid,upload_dir,comment,disabled\n"
                "alice,,!,,,upload,Finance,false\n"
            ),
        ),
    ],
)
def test_kubernetes_and_helm_sync_local_text_provider(
    tmp_path: Path,
    provider_type: ProviderType,
    filename: str,
    provider_text: str,
) -> None:
    """Sync Kubernetes YAML/CSV provider PVCs from local declarative provider files."""
    config = default_project_config("prod", provider_type)
    config.deploy.target = DeployTarget.KUBERNETES
    (tmp_path / filename).write_text(provider_text, encoding="utf-8")

    manifests = kubernetes_manifests(config, tmp_path)
    manifest_text = kubernetes_manifest_text(config, tmp_path)
    values = helm_values_model(config, tmp_path)
    configmap = next(manifest for manifest in manifests if manifest["kind"] == "ConfigMap")
    statefulset = next(manifest for manifest in manifests if manifest["kind"] == "StatefulSet")
    init_container = statefulset["spec"]["template"]["spec"]["initContainers"][0]
    init_command = init_container["command"][-1]

    assert "alice" in manifest_text
    assert configmap["data"][filename] == provider_text
    assert f"cp /config/{filename} /etc/sftpwarden/provider-data/{filename}" in init_command
    assert f"test -f /etc/sftpwarden/provider-data/{filename}" not in init_command
    assert {"name": "config", "mountPath": "/config", "readOnly": True} in init_container[
        "volumeMounts"
    ]
    assert values["provider"]["bootstrapContent"] == provider_text


def test_kubernetes_text_provider_sync_validates_local_file(tmp_path: Path) -> None:
    """Fail before deploy when the local declarative provider file is invalid."""
    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    (tmp_path / "users.yaml").write_text("users:\n- username: ''\n", encoding="utf-8")

    with pytest.raises(ProviderError, match="Invalid YAML provider file"):
        kubernetes_manifest_text(config, tmp_path)


def test_helm_values_model_reserves_runtime_replicas() -> None:
    """Generate Helm values with reserved single-replica runtime settings."""
    config = default_project_config("prod")
    config.kubernetes.release = "prod"
    config.kubernetes.data_storage_size = "50Gi"
    config.kubernetes.liveness_probe.period_seconds = 45

    values = helm_values_model(config)

    assert values["runtime"]["replicas"] == 1
    assert values["kubernetes"]["release"] == "prod"
    assert values["persistence"]["data"]["size"] == "50Gi"
    assert values["probes"]["startup"]["failureThreshold"] == 30
    assert values["probes"]["liveness"]["periodSeconds"] == 45
    assert values["provider"]["bootstrapContent"] == "schema_version: 2\nusers: []\n"
    assert "sftpwardenConfig" in values

    external = default_project_config(
        "prod",
        ProviderType.POSTGRESQL,
        dsn="postgresql://user:pass@db/sftp",
    )
    external_values = helm_values_model(external)
    assert external_values["provider"]["bootstrapContent"] == ""
    assert external_values["persistence"]["provider"]["enabled"] is False


def test_kubernetes_rendering_uses_resolved_runtime_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render local source images in development and GHCR images in packaged installs."""
    config = default_project_config("prod")

    local_values = helm_values_model(config)
    local_statefulset = next(
        manifest for manifest in kubernetes_manifests(config) if manifest["kind"] == "StatefulSet"
    )

    assert local_values["image"] == {
        "repository": "sftpwarden",
        "tag": "local",
        "pullPolicy": "IfNotPresent",
    }
    assert (
        local_statefulset["spec"]["template"]["spec"]["containers"][0]["image"]
        == "sftpwarden:local"
    )

    monkeypatch.setattr(compose_module, "LOCAL_RUNTIME_DOCKERFILE", tmp_path / "missing")
    monkeypatch.setattr(compose_module, "get_version", lambda: "9.9.9")

    packaged_values = helm_values_model(config)
    packaged_statefulset = next(
        manifest for manifest in kubernetes_manifests(config) if manifest["kind"] == "StatefulSet"
    )

    assert packaged_values["image"] == {
        "repository": "ghcr.io/kithuto/sftpwarden",
        "tag": "9.9.9",
        "pullPolicy": "IfNotPresent",
    }
    assert packaged_statefulset["spec"]["template"]["spec"]["initContainers"][0]["image"] == (
        "ghcr.io/kithuto/sftpwarden:9.9.9"
    )


def test_init_deploy_flag_persists_compose_kube_and_helm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist init deploy selections for Compose, manifests, and Helm."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()

    compose_root = tmp_path / "compose"
    kube_root = tmp_path / "kube"
    helm_root = tmp_path / "helm"
    compose = runner.invoke(app, ["init", "compose", "--root", str(compose_root), "--yes"])
    kube = runner.invoke(
        app, ["init", "kube", "--root", str(kube_root), "--deploy", "kube", "--yes"]
    )
    helm = runner.invoke(app, ["init", "helm", "--root", str(helm_root), "-d", "helm", "--yes"])

    assert compose.exit_code == 0, compose.output
    assert kube.exit_code == 0, kube.output
    assert helm.exit_code == 0, helm.output
    assert load_config(compose_root / "sftpwarden.yaml").deploy.target == "compose"
    kube_config = load_config(kube_root / "sftpwarden.yaml")
    helm_config = load_config(helm_root / "sftpwarden.yaml")
    assert kube_config.deploy.target == "kubernetes"
    assert kube_config.kubernetes.mode == "manifests"
    assert helm_config.deploy.target == "kubernetes"
    assert helm_config.kubernetes.mode == "helm"
    assert not (kube_root / "docker-compose.yml").exists()
    assert not (helm_root / "docker-compose.yml").exists()


def test_init_kubernetes_namespace_option_uses_existing_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store an explicit namespace and verify it before writing the project."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        calls.append(command)
        return CommandResult(command, 0, "namespace exists\n", "")

    monkeypatch.setattr(init_commands, "run", fake_run)
    root = tmp_path / "kube"
    result = CliRunner().invoke(
        app,
        [
            "init",
            "kube",
            "--root",
            str(root),
            "--deploy",
            "kube",
            "--namespace",
            "team-sftp",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_config(root / "sftpwarden.yaml").kubernetes.namespace == "team-sftp"
    assert calls == [["kubectl", "get", "namespace", "team-sftp"]]


def test_init_helm_namespace_option_uses_existing_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply the namespace init check to Helm projects too."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        calls.append(command)
        return CommandResult(command, 0, "namespace exists\n", "")

    monkeypatch.setattr(init_commands, "run", fake_run)
    root = tmp_path / "helm"
    result = CliRunner().invoke(
        app,
        [
            "init",
            "helm",
            "--root",
            str(root),
            "--deploy",
            "helm",
            "--namespace",
            "team-sftp",
            "--yes",
        ],
    )
    config = load_config(root / "sftpwarden.yaml")

    assert result.exit_code == 0, result.output
    assert config.kubernetes.mode == "helm"
    assert config.kubernetes.namespace == "team-sftp"
    assert calls == [["kubectl", "get", "namespace", "team-sftp"]]


def test_init_kubernetes_namespace_creation_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a missing Kubernetes namespace when init is allowed to do so."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        calls.append(command)
        if command[:3] == ["kubectl", "get", "namespace"]:
            return CommandResult(command, 1, "", 'namespaces "team-sftp" not found')
        return CommandResult(command, 0, "namespace/team-sftp created\n", "")

    monkeypatch.setattr(init_commands, "run", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "init",
            "kube",
            "--root",
            str(tmp_path / "kube"),
            "--deploy",
            "kube",
            "--namespace",
            "team-sftp",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert ["kubectl", "create", "namespace", "team-sftp"] in calls
    assert "Created Kubernetes namespace" in result.output


def test_init_default_kubernetes_namespace_is_created_with_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the default sftpwarden namespace and create it automatically with --yes."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        calls.append(command)
        if command[:3] == ["kubectl", "get", "namespace"]:
            return CommandResult(command, 1, "", 'namespaces "sftpwarden" not found')
        return CommandResult(command, 0, "namespace/sftpwarden created\n", "")

    monkeypatch.setattr(init_commands, "run", fake_run)
    root = tmp_path / "kube"
    result = CliRunner().invoke(
        app,
        ["init", "kube", "--root", str(root), "--deploy", "kube", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert load_config(root / "sftpwarden.yaml").kubernetes.namespace == "sftpwarden"
    assert calls == [
        ["kubectl", "get", "namespace", "sftpwarden"],
        ["kubectl", "create", "namespace", "sftpwarden"],
    ]


def test_kubernetes_user_mutation_reports_deploy_instead_of_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Save Kubernetes provider changes locally and guide operators to deploy."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "kube"
    runner = CliRunner()
    init = runner.invoke(app, ["init", "kube", "--root", str(root), "--deploy", "kube", "--yes"])
    refresh_calls: list[object] = []
    monkeypatch.setattr(
        cli_workflows,
        "refresh_context",
        lambda entry, *, dry_run=False: refresh_calls.append(entry) or "refreshed",
    )

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            "!",
            "--context",
            "kube",
        ],
    )

    assert init.exit_code == 0, init.output
    assert result.exit_code == 0, result.output
    assert "Saved provider change locally" in result.output
    assert "sftpwarden deploy" in result.output
    assert refresh_calls == []
    assert "alice" in (root / "users.yaml").read_text(encoding="utf-8")


def test_init_kubernetes_namespace_refusal_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abort init when the namespace is missing and the user refuses creation."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(init_commands.Confirm, "ask", lambda *_args, **_kwargs: False)

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        return CommandResult(command, 1, "", 'namespaces "team-sftp" not found')

    monkeypatch.setattr(init_commands, "run", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "init",
            "kube",
            "--root",
            str(tmp_path / "kube"),
            "--deploy",
            "kube",
            "--namespace",
            "team-sftp",
        ],
    )

    assert result.exit_code == 1
    assert "Kubernetes namespace does not exist: team-sftp" in result.output
    assert "pass an existing namespace" in result.output
    assert "--namespace" in result.output


def test_config_command_updates_kubernetes_values_and_rejects_replicas(
    tmp_path: Path,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Allow Kubernetes config edits while rejecting unsupported replicas."""
    runner = CliRunner()
    root = register_kubernetes_project(
        local_project_factory, tmp_path, "dev", KubernetesMode.MANIFESTS
    )

    update = runner.invoke(app, ["config", "kubernetes.namespace", "sftp"])
    resize = runner.invoke(app, ["config", "kubernetes.data_storage_size", "50Gi"])
    invalid = runner.invoke(app, ["config", "kubernetes.replicas", "2"])
    loaded = load_config(root / "sftpwarden.yaml")

    assert update.exit_code == 0, update.output
    assert resize.exit_code == 0, resize.output
    assert loaded.kubernetes.namespace == "sftp"
    assert loaded.kubernetes.data_storage_size == "50Gi"
    assert invalid.exit_code == 1
    assert "Kubernetes replicas > 1 are not supported yet" in invalid.output
    assert "Set kubernetes.replicas to 1" in invalid.output


def test_deploy_dry_run_dispatches_to_kubernetes_and_helm(
    tmp_path: Path,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Expose dry-run deployment actions for manifests and Helm in JSON."""
    runner = CliRunner()
    register_kubernetes_project(local_project_factory, tmp_path, "kube", KubernetesMode.MANIFESTS)
    kube_result = runner.invoke(app, ["deploy", "--context", "kube", "--dry-run", "--json"])
    register_kubernetes_project(local_project_factory, tmp_path, "helm", KubernetesMode.HELM)
    helm_result = runner.invoke(app, ["deploy", "--context", "helm", "--dry-run", "--json"])

    kube_data = json.loads(kube_result.output)
    helm_data = json.loads(helm_result.output)

    assert kube_result.exit_code == 0, kube_result.output
    assert helm_result.exit_code == 0, helm_result.output
    assert kube_data["plan"]["target"] == "kubernetes"
    assert kube_data["plan"]["mode"] == "manifests"
    kube_plan_commands = [
        action["command"] for action in kube_data["plan"]["actions"] if action["command"]
    ]
    helm_plan_commands = [
        action["command"] for action in helm_data["plan"]["actions"] if action["command"]
    ]
    assert any(command[0] == "kubectl" and "apply" in command for command in kube_plan_commands)
    assert any(command[0] == "kubectl" and "rollout" in command for command in kube_plan_commands)
    assert helm_data["plan"]["mode"] == "helm"
    assert any(command[0] == "helm" and "upgrade" in command for command in helm_plan_commands)
    assert any(command[0] == "kubectl" and "rollout" in command for command in helm_plan_commands)


def test_kubectl_and_helm_command_generation() -> None:
    """Include namespace and kube-context flags in generated commands."""
    config = default_project_config("prod")
    config.kubernetes.namespace = "sftp"
    config.kubernetes.kube_context = "kind-sftp"

    assert kubectl_command(config, ["get", "pods"], namespace="sftp") == [
        "kubectl",
        "--context",
        "kind-sftp",
        "-n",
        "sftp",
        "get",
        "pods",
    ]
    assert helm_command(config, ["lint", "charts/sftpwarden"])[:3] == [
        "helm",
        "--kube-context",
        "kind-sftp",
    ]


def test_helm_chart_reference_uses_local_chart_or_versioned_oci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the source checkout chart locally and the versioned OCI chart in wheels."""
    local_chart = tmp_path / "charts" / "sftpwarden"
    local_chart.mkdir(parents=True)
    monkeypatch.setattr(deploy_module, "LOCAL_CHART_PATH", local_chart)

    local = helm_chart_reference()

    assert local.local is True
    assert local.command_args() == [str(local_chart)]

    monkeypatch.setattr(deploy_module, "LOCAL_CHART_PATH", tmp_path / "missing")
    monkeypatch.setattr(deploy_module, "get_version", lambda: "9.9.9")

    oci = helm_chart_reference()

    assert oci.local is False
    assert oci.command_args() == [HELM_OCI_CHART_REF, "--version", "9.9.9"]

    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = KubernetesMode.HELM
    plan = deploy_module.helm_deployment_plan(
        ContextEntry(
            name="prod",
            type=ContextType.LOCAL,
            root=str(tmp_path),
            config=str(tmp_path / "sftpwarden.yaml"),
        ),
        config,
    )
    command = next(
        action.command or []
        for action in plan.actions
        if action.command and action.command[0] == "helm"
    )
    assert command[command.index(HELM_OCI_CHART_REF) + 1 :][:2] == ["--version", "9.9.9"]


def test_helm_lint_pulls_oci_chart_when_local_chart_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pull the published OCI chart before linting installed-package charts."""
    config = default_project_config("prod")
    version = get_version()
    chart = HelmChartReference(reference=HELM_OCI_CHART_REF, version=version)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        calls.append(command)
        return CommandResult(command, 0, "ok\n", "")

    monkeypatch.setattr(helm_commands, "run", fake_run)

    result = helm_commands._run_helm_lint(config, chart, cwd=str(tmp_path))

    assert result.returncode == 0
    assert calls[0][:5] == ["helm", "pull", HELM_OCI_CHART_REF, "--version", version]
    assert calls[1][0:2] == ["helm", "lint"]
    assert Path(calls[1][2]).name == "sftpwarden"


def test_helm_lint_requires_version_for_oci_chart(tmp_path: Path) -> None:
    """Require a chart version before linting the published OCI chart."""
    config = default_project_config("prod")
    chart = HelmChartReference(reference=HELM_OCI_CHART_REF)

    with pytest.raises(SFTPWardenError, match="Cannot lint the published Helm chart"):
        helm_commands._run_helm_lint(config, chart, cwd=str(tmp_path))


def test_helm_lint_returns_oci_pull_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return the Helm pull failure without attempting to lint a missing chart."""
    config = default_project_config("prod")
    version = get_version()
    chart = HelmChartReference(reference=HELM_OCI_CHART_REF, version=version)
    calls: list[list[str]] = []

    def failing_run(command: list[str], **_kwargs) -> CommandResult:
        calls.append(command)
        return CommandResult(command, 1, "", "pull failed")

    monkeypatch.setattr(helm_commands, "run", failing_run)

    result = helm_commands._run_helm_lint(config, chart, cwd=str(tmp_path))

    assert result.returncode == 1
    assert result.stderr == "pull failed"
    assert len(calls) == 1
    assert calls[0][:7] == [
        "helm",
        "pull",
        HELM_OCI_CHART_REF,
        "--version",
        version,
        "--untar",
        "--untardir",
    ]


def test_cli_reports_missing_kubectl_and_helm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Translate missing kubectl and Helm executables into actionable errors."""
    runner = CliRunner()
    root = register_kubernetes_project(
        local_project_factory, tmp_path, "dev", KubernetesMode.MANIFESTS
    )

    def missing(command: list[str], **_kwargs) -> CommandResult:
        return CommandResult(command, 127, "", f"Executable not found: {command[0]}")

    monkeypatch.setattr(kube_commands, "run", missing)
    kube = runner.invoke(app, ["kube", "apply"])
    status = runner.invoke(app, ["kube", "status"])
    doctor = runner.invoke(app, ["kube", "doctor"])

    config = load_config(root / "sftpwarden.yaml")
    config.kubernetes.mode = KubernetesMode.HELM
    write_config(root / "sftpwarden.yaml", config)
    monkeypatch.setattr(helm_commands, "run", missing)
    helm = runner.invoke(app, ["helm", "lint"])

    assert kube.exit_code == 1
    assert "Required executable not found: kubectl" in kube.output
    assert status.exit_code == 0
    assert "Required executable not found: kubectl" in status.output
    assert "Install kubectl and try again." in status.output
    assert doctor.exit_code == 0
    assert "Required executable not found: kubectl" in doctor.output
    assert "Install kubectl and try again." in doctor.output
    assert helm.exit_code == 1
    assert "Required executable not found: helm" in helm.output


def test_chart_schema_limits_runtime_replicas() -> None:
    """Constrain Helm runtime replicas, PVC sizing, and probe timings."""
    schema = json.loads(Path("charts/sftpwarden/values.schema.json").read_text(encoding="utf-8"))

    replicas = schema["properties"]["runtime"]["properties"]["replicas"]
    data_size = schema["properties"]["persistence"]["properties"]["data"]["properties"]["size"]
    probe = schema["definitions"]["probe"]["properties"]
    assert replicas["maximum"] == 1
    assert replicas["minimum"] == 1
    assert data_size["description"].startswith("Requested storage size")
    assert probe["failureThreshold"]["minimum"] == 1
    assert probe["periodSeconds"]["minimum"] == 1
    assert probe["timeoutSeconds"]["minimum"] == 1


def test_example_values_are_valid_yaml() -> None:
    """Keep Kubernetes example values valid and aligned with v1.3 defaults."""
    values = yaml.safe_load(Path("examples/kubernetes/values-postgresql.yaml").read_text())

    assert values["runtime"]["replicas"] == 1
    assert values["provider"]["type"] == "postgresql"
    assert values["provider"]["createDsnSecret"] is False
    assert values["probes"]["startup"]["failureThreshold"] == 30
    assert values["probes"]["liveness"]["periodSeconds"] == 30
    assert "type: postgresql" in values["sftpwardenConfig"]
    assert 'dsn: "${SFTPWARDEN_PROVIDER_DSN}"' in values["sftpwardenConfig"]


def test_config_error_conversion_and_init_deploy_validation() -> None:
    """Convert replica validation and bad init deploy modes into CLI errors."""
    with pytest.raises(ValidationError) as exc_info:
        SFTPWardenConfig.model_validate({"project": {"name": "dev"}, "kubernetes": {"replicas": 3}})

    converted = validation_error_to_config_error(exc_info.value, Path("sftpwarden.yaml"))

    assert "Kubernetes replicas > 1 are not supported yet" in converted.message
    assert converted.suggestion == "Set kubernetes.replicas to 1."
    invalid = CliRunner().invoke(app, ["init", "dev", "--deploy", "nomad", "--yes"])
    assert invalid.exit_code == 1
    assert "Unsupported deploy method" in invalid.output


def test_render_helpers_cover_image_without_tag_and_storage_class() -> None:
    """Render image references without tags and explicit storage classes."""
    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.storage_class = "fast"
    config.kubernetes.data_storage_size = "50Gi"

    assert split_image("registry.example.com/sftpwarden") == (
        "registry.example.com/sftpwarden",
        None,
    )
    pvc = next(
        manifest
        for manifest in kubernetes_manifests(config)
        if manifest["kind"] == "PersistentVolumeClaim"
        and manifest["metadata"]["name"] == "prod-data"
    )
    assert pvc["spec"]["storageClassName"] == "fast"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "50Gi"


def test_deploy_services_cover_apply_paths_and_diff_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover Kubernetes and Helm apply paths plus rendered diff detection."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = KubernetesMode.MANIFESTS
    write_config(root / "sftpwarden.yaml", config)
    entry = local_context("prod", root, ProviderType.YAML)
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], *, cwd: str | None = None) -> CommandResult:
        calls.append((command, cwd))
        return CommandResult(command, 0, "ok", "")

    plan = deployment_plan(entry)
    text = plan.text()
    applied = apply_deployment_plan(entry, runner=runner)

    assert "Deploy target: kubernetes" in text
    assert applied == "Applied Kubernetes manifests for prod."
    assert config.kubernetes.namespace == "sftpwarden"
    assert any(
        command[:3] == ["kubectl", "-n", "sftpwarden"] and "apply" in command
        for command, _cwd in calls
    )
    assert calls[-1][0] == [
        "kubectl",
        "-n",
        "sftpwarden",
        "rollout",
        "restart",
        "statefulset/prod",
    ]
    assert kubernetes_rendered_manifest_diff_reason(config, root) is None
    (root / "kubernetes.yml").write_text("stale", encoding="utf-8")
    assert kubernetes_rendered_manifest_diff_reason(config, root) == (
        "kubernetes.yml differs from current configuration"
    )
    (root / "kubernetes.yml").unlink()
    assert kubernetes_rendered_manifest_diff_reason(config, root) == "kubernetes.yml is missing"

    config.kubernetes.mode = KubernetesMode.HELM
    write_config(root / "sftpwarden.yaml", config)
    calls.clear()
    helm_applied = apply_deployment_plan(entry, runner=runner)
    assert helm_applied == "Deployed prod with Helm."
    assert any(command[0] == "helm" and "upgrade" in command for command, _cwd in calls)
    assert calls[-1][0][0] == "kubectl"
    assert "rollout" in calls[-1][0]
    assert helm_values_diff_reason(config, root) is None
    (root / "values.yaml").write_text("stale", encoding="utf-8")
    assert helm_values_diff_reason(config, root) == "values.yaml differs from current configuration"
    (root / "values.yaml").unlink()
    assert helm_values_diff_reason(config, root) == "values.yaml is missing"


def test_deploy_services_cover_compose_and_error_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover Compose deployment plus context and command failure edges."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "compose"
    root.mkdir()
    config = default_project_config("compose")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text("users: []\n", encoding="utf-8")
    entry = local_context("compose", root, ProviderType.YAML)

    def runner(command: list[str], *, cwd: str | None = None) -> CommandResult:
        return CommandResult(command, 0, "ok", "")

    assert apply_deployment_plan(entry, runner=runner) == "Deployed compose with Docker Compose."
    assert legacy_deploy_context(entry, dry_run=True).startswith("docker compose")
    assert DeployPlan([["docker", "compose", "ps"]]).text() == "docker compose ps"

    no_config = ContextEntry(name="plain", type=ContextType.LOCAL, root=str(root), config="")
    with pytest.raises(ContextError):
        apply_deployment_plan(no_config, runner=runner)
    with pytest.raises(ContextError):
        kubernetes_deployment_plan(
            ContextEntry(
                name="remote",
                type=ContextType.REMOTE,
                root="",
                config=str(root / "sftpwarden.yaml"),
            )
        )
    with pytest.raises(ContextError):
        apply_deployment_plan(
            ContextEntry(
                name="noroot", type=ContextType.LOCAL, root="", config=str(root / "sftpwarden.yaml")
            ),
            runner=runner,
        )

    failing_plan = DeploymentPlan(
        context="dev",
        target="kubernetes",
        mode="manifests",
        namespace="sftpwarden",
        release="dev",
        actions=[DeployAction("fail", ["kubectl", "apply"])],
    )
    monkeypatch.setattr(
        "sftpwarden.services.deploy.kubernetes_deployment_plan",
        lambda *_args, **_kwargs: failing_plan,
    )

    def fail_runner(command: list[str], *, cwd: str | None = None) -> CommandResult:
        return CommandResult(command, 1, "", "namespace not found")

    config.deploy.target = DeployTarget.KUBERNETES
    write_config(root / "sftpwarden.yaml", config)
    with pytest.raises(RuntimeError, match="Kubernetes namespace was not found"):
        apply_deployment_plan(entry, runner=fail_runner)


def test_deploy_services_cover_remaining_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover remaining deploy service branches for local context handling."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    write_config(root / "sftpwarden.yaml", config)
    no_config = ContextEntry(name="plain", type=ContextType.LOCAL, root=str(root), config="")
    simple_plan = DeploymentPlan(
        context="plain",
        target="compose",
        mode="compose",
        namespace=None,
        release=None,
        actions=[DeployAction("compose", ["docker", "compose", "ps"])],
    )
    calls: list[list[str]] = []

    def runner(command: list[str], *, cwd: str | None = None) -> CommandResult:
        calls.append(command)
        return CommandResult(command, 0, "ok", "")

    monkeypatch.setattr(deploy_module, "compose_deployment_plan", lambda *_args: simple_plan)
    assert apply_deployment_plan(no_config, runner=runner) == "Deployed plain with Docker Compose."
    assert calls == [["docker", "compose", "ps"]]

    with pytest.raises(ContextError, match="no local root"):
        apply_deployment_plan(
            ContextEntry(
                name="prod", type=ContextType.LOCAL, root="", config=str(root / "sftpwarden.yaml")
            ),
            runner=runner,
        )
    assert ensure_helm_values(config, root).name == "values.yaml"
    with pytest.raises(ContextError, match="no local sftpwarden.yaml"):
        deploy_module._context_config(ContextEntry(name="empty", type=ContextType.LOCAL))


def test_core_deploy_wrapper_and_kubernetes_change_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose Kubernetes and Helm config-change reasons through core deploy."""
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("prod")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.mode = KubernetesMode.HELM
    entry = ContextEntry(
        name="prod",
        type=ContextType.LOCAL,
        root=str(root),
        config=str(root / "sftpwarden.yaml"),
        provider=ProviderType.YAML,
    )
    plan = DeploymentPlan(
        context="prod",
        target="kubernetes",
        mode="helm",
        namespace="sftpwarden",
        release="prod",
        actions=[DeployAction("helm")],
    )

    monkeypatch.setattr(core_commands, "deployment_plan", lambda *_args: plan)
    monkeypatch.setattr(core_commands, "apply_deployment_plan", lambda *_args: "applied")
    assert core_commands.deploy_context(entry) == "applied"

    helm_reasons = core_commands.deploy_config_change_reasons(entry, config)
    assert "deploy target is kubernetes" in helm_reasons
    assert "kubernetes mode is helm" in helm_reasons
    assert "values.yaml is missing" in helm_reasons

    config.kubernetes.mode = KubernetesMode.MANIFESTS
    manifest_reasons = core_commands.deploy_config_change_reasons(entry, config)
    assert "kubernetes mode is manifests" in manifest_reasons
    assert "kubernetes.yml is missing" in manifest_reasons

    monkeypatch.setattr(core_commands, "resolve_context", lambda **_kwargs: entry)
    monkeypatch.setattr(core_commands, "deployment_plan", lambda *_args: plan)
    monkeypatch.setattr(core_commands, "deploy_context", lambda *_args: "deployed")
    result = CliRunner().invoke(app, ["deploy", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["result"] == "deployed"


def test_translate_command_failure_messages() -> None:
    """Map common kubectl and Helm failures to user-facing messages."""
    cases = [
        (CommandResult(["kubectl"], 127, "", ""), "Required executable not found"),
        (CommandResult(["kubectl"], 1, "", "context kind does not exist"), "context was not found"),
        (CommandResult(["kubectl"], 1, "", "forbidden by RBAC"), "RBAC permissions"),
        (CommandResult(["kubectl"], 1, "", "storageclass fast not found"), "storage class"),
        (
            CommandResult(
                ["docker", "compose", "up"],
                1,
                "",
                "docker: 'compose' is not a docker command",
            ),
            "Docker Compose v2 is not available",
        ),
        (
            CommandResult(["docker", "compose", "pull"], 1, "", "manifest unknown"),
            "Docker image could not be pulled",
        ),
        (
            CommandResult(
                ["docker", "compose", "up"], 1, "", "Cannot connect to the Docker daemon"
            ),
            "Docker daemon is not reachable",
        ),
        (
            CommandResult(["docker", "compose", "ps"], 1, "", "permission denied for docker"),
            "Docker daemon permissions are insufficient",
        ),
        (CommandResult(["kubectl"], 1, "", "plain failure"), "Deployment command failed"),
    ]

    for result, message in cases:
        assert message in translate_command_failure(result).message


def test_helm_cli_commands_cover_success_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Cover successful Helm CLI values, template, lint, upgrade, and uninstall."""
    runner = CliRunner()
    root = register_kubernetes_project(local_project_factory, tmp_path, "helm", KubernetesMode.HELM)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(command, 0, "rendered\n", "")

    monkeypatch.setattr(helm_commands, "run", fake_run)

    commands = [
        ["helm", "values", "--context", "helm", "--write"],
        ["helm", "values", "--context", "helm"],
        ["helm", "template", "--context", "helm", "--json"],
        ["helm", "template", "--context", "helm"],
        ["helm", "lint", "--context", "helm"],
        ["helm", "upgrade", "--context", "helm", "--dry-run"],
        ["helm", "upgrade", "--context", "helm", "--install", "--dry-run", "--json"],
        ["helm", "upgrade", "--context", "helm", "--json"],
        ["helm", "upgrade", "--context", "helm", "--install"],
        ["helm", "uninstall", "--context", "helm", "--dry-run"],
        ["helm", "uninstall", "--context", "helm", "--yes"],
    ]
    results = [runner.invoke(app, command) for command in commands]

    for result in results:
        assert result.exit_code == 0, result.output
    assert (root / "values.yaml").exists()
    assert any(call[0][0] == "helm" and "template" in call[0] for call in calls)
    assert any(call[0][0] == "kubectl" and "rollout" in call[0] for call in calls)
    assert any(call[0][0] == "helm" and "uninstall" in call[0] for call in calls)
    assert "--install" not in results[5].output


def test_helm_cli_commands_cover_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover Helm CLI loader and local-root error paths."""
    runner = CliRunner()
    config = default_project_config("remote")
    no_root = SimpleNamespace(name="remote", root=None)
    original_loader = helm_commands._load_context_config
    monkeypatch.setattr(helm_commands, "_load_context_config", lambda *_args: (no_root, config))
    values = runner.invoke(app, ["helm", "values", "--write"])
    template = runner.invoke(app, ["helm", "template"])

    assert values.exit_code == 1
    assert template.exit_code == 1

    def failing_loader(*_args):
        raise SFTPWardenError("broken")

    monkeypatch.setattr(helm_commands, "_load_context_config", failing_loader)
    assert runner.invoke(app, ["helm", "lint"]).exit_code == 1
    assert runner.invoke(app, ["helm", "upgrade"]).exit_code == 1
    assert runner.invoke(app, ["helm", "uninstall"]).exit_code == 1

    monkeypatch.setattr(
        helm_commands,
        "resolve_context",
        lambda **_kwargs: SimpleNamespace(name="remote", config=None),
    )
    monkeypatch.setattr(helm_commands, "_load_context_config", original_loader)
    with pytest.raises(SFTPWardenError, match="no local sftpwarden.yaml"):
        helm_commands._load_context_config(None, None)


def test_helm_cli_commands_cover_command_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Translate failing Helm commands and declined uninstall confirmations."""
    runner = CliRunner()
    register_kubernetes_project(local_project_factory, tmp_path, "helm", KubernetesMode.HELM)

    def failing_run(command: list[str], **_kwargs) -> CommandResult:
        return CommandResult(command, 1, "", "namespace missing")

    monkeypatch.setattr(helm_commands, "run", failing_run)
    assert runner.invoke(app, ["helm", "template", "--context", "helm"]).exit_code == 1
    assert runner.invoke(app, ["helm", "upgrade", "--context", "helm"]).exit_code == 1
    assert runner.invoke(app, ["helm", "uninstall", "--context", "helm", "--yes"]).exit_code == 1

    monkeypatch.setattr(helm_commands.Confirm, "ask", lambda *_args, **_kwargs: False)
    assert runner.invoke(app, ["helm", "uninstall", "--context", "helm"]).exit_code == 1


def test_kube_cli_commands_cover_success_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Cover successful Kubernetes CLI render, apply, status, logs, doctor, and delete."""
    runner = CliRunner()
    root = register_kubernetes_project(
        local_project_factory, tmp_path, "kube", KubernetesMode.MANIFESTS
    )
    config = load_config(root / "sftpwarden.yaml")
    config.kubernetes.storage_class = "fast"
    write_config(root / "sftpwarden.yaml", config)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(command, 0, "ok\n", "")

    monkeypatch.setattr(kube_commands, "run", fake_run)
    commands = [
        ["kube", "render", "--context", "kube"],
        ["kube", "apply", "--context", "kube", "--dry-run", "--json"],
        ["kube", "apply", "--context", "kube", "--dry-run"],
        ["kube", "apply", "--context", "kube", "--json"],
        ["kube", "apply", "--context", "kube"],
        ["kube", "status", "--context", "kube", "--json"],
        ["kube", "status", "--context", "kube"],
        ["kube", "logs", "--context", "kube"],
        ["kube", "logs", "--context", "kube", "--follow"],
        ["kube", "doctor", "--context", "kube", "--json"],
        ["kube", "doctor", "--context", "kube"],
        ["kube", "delete", "--context", "kube", "--dry-run"],
        ["kube", "delete", "--context", "kube", "--yes"],
    ]
    results = [runner.invoke(app, command) for command in commands]

    for result in results:
        assert result.exit_code == 0, result.output
    assert any(call[0][:3] == ["kubectl", "-n", "sftpwarden"] for call in calls)
    assert any(call[1].get("capture_output") is False for call in calls)


def test_kube_cli_commands_cover_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Cover Kubernetes CLI command failures and destructive confirmation paths."""
    runner = CliRunner()
    root = register_kubernetes_project(
        local_project_factory, tmp_path, "kube", KubernetesMode.MANIFESTS
    )

    def failing_run(command: list[str], **_kwargs) -> CommandResult:
        return CommandResult(command, 1, "", "namespace missing")

    monkeypatch.setattr(kube_commands, "run", failing_run)
    apply_failure = runner.invoke(app, ["kube", "apply", "--context", "kube"])
    logs_failure = runner.invoke(app, ["kube", "logs", "--context", "kube"])
    delete_failure = runner.invoke(app, ["kube", "delete", "--context", "kube", "--yes"])
    assert apply_failure.exit_code == 1
    assert logs_failure.exit_code == 1
    assert delete_failure.exit_code == 1

    monkeypatch.setattr(kube_commands.Confirm, "ask", lambda *_args, **_kwargs: False)
    assert runner.invoke(app, ["kube", "delete", "--context", "kube"]).exit_code == 1

    no_root = ContextEntry(
        name="remote",
        type=ContextType.LOCAL,
        root="",
        config=str(root / "sftpwarden.yaml"),
        provider=ProviderType.YAML,
    )
    monkeypatch.setattr(kube_commands, "resolve_context", lambda **_kwargs: no_root)
    assert runner.invoke(app, ["kube", "apply"]).exit_code == 1

    no_config = SimpleNamespace(name="remote", root=str(root), config=None)
    monkeypatch.setattr(kube_commands, "resolve_context", lambda **_kwargs: no_config)
    assert runner.invoke(app, ["kube", "apply"]).exit_code == 1
    assert runner.invoke(app, ["kube", "delete"]).exit_code == 1
    with pytest.raises(SFTPWardenError, match="no local sftpwarden.yaml"):
        kube_commands._load_config(None, None)

    config = load_config(root / "sftpwarden.yaml")
    empty_command_plan = DeploymentPlan(
        context="kube",
        target="kubernetes",
        mode="manifests",
        namespace="sftpwarden",
        release="kube",
        actions=[DeployAction("render"), DeployAction("apply")],
    )
    monkeypatch.setattr(
        kube_commands,
        "resolve_context",
        lambda **_kwargs: local_context("kube", root, ProviderType.YAML),
    )
    monkeypatch.setattr(kube_commands, "load_config", lambda *_args: config)
    monkeypatch.setattr(
        kube_commands, "kubernetes_deployment_plan", lambda *_args: empty_command_plan
    )
    assert runner.invoke(app, ["kube", "apply"]).exit_code == 1


def test_kube_cli_commands_cover_loader_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return CLI errors when Kubernetes command config loading fails."""
    runner = CliRunner()

    def failing_loader(*_args):
        raise SFTPWardenError("broken")

    monkeypatch.setattr(kube_commands, "_load_config", failing_loader)
    assert runner.invoke(app, ["kube", "render"]).exit_code == 1
    assert runner.invoke(app, ["kube", "status"]).exit_code == 1
    assert runner.invoke(app, ["kube", "doctor"]).exit_code == 1
