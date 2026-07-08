from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.cli_commands.context as context_commands
import sftpwarden.cli_commands.helm as helm_commands
import sftpwarden.cli_commands.init as init_commands
from sftpwarden.cli import app
from sftpwarden.cli_commands.provider import print_provider_mutation_result
from sftpwarden.config import (
    DeployTarget,
    ProviderType,
    default_project_config,
    provider_local_path,
)
from sftpwarden.config.global_config import load_global_config, save_global_config
from sftpwarden.contexts import ContextRegistry, local_context
from sftpwarden.render import kubernetes as kubernetes_render
from sftpwarden.services.provider_transfer import ProviderMutationResult
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils.errors import SFTPWardenError


def test_context_warns_when_watcher_kept_without_local_sync_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    config = load_global_config()
    config.watcher.installed = True
    config.watcher.mode = "systemd"
    save_global_config(config)
    registry = ContextRegistry(
        default="dev",
        contexts={"dev": local_context("dev", tmp_path / "project", ProviderType.YAML)},
    )
    monkeypatch.setattr(context_commands.Confirm, "ask", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        context_commands,
        "uninstall_watcher",
        lambda: (_ for _ in ()).throw(AssertionError("uninstall should not run")),
    )

    context_commands.handle_watcher_without_local_sync_targets(registry, yes=False)

    assert "there are no remote local-sync contexts left" in capsys.readouterr().out


def test_helm_upgrade_reports_missing_upgrade_command(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyPlan:
        actions: list[object] = []

        def as_dict(self) -> dict[str, object]:
            return {}

    entry = local_context("dev", Path("project"), ProviderType.YAML)
    config = default_project_config("dev", deploy_target=DeployTarget.KUBERNETES)
    monkeypatch.setattr(helm_commands, "_load_context_config", lambda *_args: (entry, config))
    monkeypatch.setattr(
        helm_commands,
        "apply_provider_schema_before_deploy",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(helm_commands, "helm_deployment_plan", lambda *_args: EmptyPlan())

    result = CliRunner().invoke(app, ["helm", "upgrade"])

    assert result.exit_code == 1
    assert "Helm upgrade command was not generated" in result.output


def test_init_rejects_namespace_for_compose_target() -> None:
    with pytest.raises(SFTPWardenError, match="--namespace is only valid"):
        init_commands.init_project_config(
            "dev",
            ProviderType.YAML,
            dsn=None,
            query=None,
            table="sftp_users",
            deploy_method="compose",
            namespace="custom",
            yes=True,
        )


def test_kubernetes_namespace_check_translates_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = default_project_config("dev", deploy_target=DeployTarget.KUBERNETES)
    monkeypatch.setattr(
        init_commands,
        "run",
        lambda _command: CommandResult(
            args=["kubectl"],
            returncode=1,
            stdout="",
            stderr="forbidden",
        ),
    )

    with pytest.raises(SFTPWardenError, match="RBAC permissions"):
        init_commands.ensure_kubernetes_namespace_for_init(
            config,
            yes=True,
            create_namespace=None,
            skip_checks=False,
        )


def test_kubernetes_namespace_creation_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = default_project_config("dev", deploy_target=DeployTarget.KUBERNETES)
    results = iter(
        [
            CommandResult(
                args=["kubectl", "get"],
                returncode=1,
                stdout="",
                stderr='Error from server (NotFound): namespaces "sftpwarden" not found',
            ),
            CommandResult(
                args=["kubectl", "create"],
                returncode=1,
                stdout="",
                stderr="cannot create namespace",
            ),
        ]
    )
    monkeypatch.setattr(init_commands, "run", lambda _command: next(results))

    with pytest.raises(SFTPWardenError, match="Deployment command failed"):
        init_commands.ensure_kubernetes_namespace_for_init(
            config,
            yes=True,
            create_namespace=True,
            skip_checks=False,
        )


def test_provider_mutation_prints_manual_action(capsys: pytest.CaptureFixture[str]) -> None:
    result = ProviderMutationResult(
        source_count=1,
        destination_count=1,
        changed=True,
        runtime_changed=False,
        manual_action="Copy the SQLite database manually.",
    )

    print_provider_mutation_result(result, dry_run=False, json_output=False)

    assert "Copy the SQLite database manually" in capsys.readouterr().out


def test_provider_local_path_accepts_absolute_host_paths(tmp_path: Path) -> None:
    config = default_project_config("dev")
    absolute_provider = tmp_path / "external-users.yaml"
    config.provider.path = str(absolute_provider)

    assert provider_local_path(tmp_path / "project", config) == absolute_provider


def test_kubernetes_bootstrap_command_initializes_non_configmap_provider(
    tmp_path: Path,
) -> None:
    config = default_project_config("dev", ProviderType.SQLITE)

    command = kubernetes_render._provider_bootstrap_command(config, tmp_path)

    assert command is not None
    assert "test -f" in command
    assert "printf %s" in command
