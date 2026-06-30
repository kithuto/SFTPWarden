from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.services.cli_workflows as workflow_services
from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    ProviderType,
    default_project_config,
    write_config,
)
from sftpwarden.contexts import local_context
from sftpwarden.refresh.core import _kubernetes_config_if_available
from sftpwarden.utils.errors import ContextError


def test_print_refresh_after_user_change_explains_helm_yaml_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "helm"
    root.mkdir()
    config = default_project_config(
        "prod",
        deploy_target=DeployTarget.KUBERNETES,
        kubernetes_mode=KubernetesMode.HELM,
    )
    write_config(root / "sftpwarden.yaml", config)
    entry = local_context("prod", root, ProviderType.YAML)
    monkeypatch.setattr(
        workflow_services,
        "refresh_context",
        lambda _entry: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    workflow_services.print_refresh_after_user_change(entry)

    assert "helm upgrade --install" in capsys.readouterr().out


def test_print_refresh_after_user_change_explains_kubernetes_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "sqlite"
    root.mkdir()
    config = default_project_config(
        "prod",
        ProviderType.SQLITE,
        deploy_target=DeployTarget.KUBERNETES,
    )
    write_config(root / "sftpwarden.yaml", config)
    entry = local_context("prod", root, ProviderType.SQLITE)
    monkeypatch.setattr(
        workflow_services,
        "refresh_context",
        lambda _entry: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    workflow_services.print_refresh_after_user_change(entry)

    assert "SQLite provider files are not copied" in capsys.readouterr().out


def test_print_refresh_after_user_change_refreshes_without_kubernetes_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "compose"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    entry_without_config = local_context("no-config", root, ProviderType.YAML).model_copy(
        update={"config": ""}
    )
    entry_compose = local_context("compose", root, ProviderType.YAML)
    calls: list[str] = []

    def fake_refresh(entry) -> str:
        calls.append(entry.name)
        return f"refreshed {entry.name}"

    monkeypatch.setattr(workflow_services, "refresh_context", fake_refresh)

    workflow_services.print_refresh_after_user_change(entry_without_config)
    workflow_services.print_refresh_after_user_change(entry_compose)

    output = capsys.readouterr().out
    assert calls == ["no-config", "compose"]
    assert "refreshed no-config" in output
    assert "refreshed compose" in output


def test_install_context_watcher_accepts_and_rejects_docker_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = local_context("prod", tmp_path / "project", ProviderType.YAML).model_copy(
        update={"watcher_required": True}
    )
    calls: list[tuple[str | None, bool, bool]] = []

    def fake_ensure_watcher(*, requested_mode, yes, allow_docker_fallback):
        calls.append((requested_mode, yes, allow_docker_fallback))
        if len(calls) == 1:
            raise workflow_services.WatcherDockerFallbackRequired("fallback")
        return "installed docker"

    monkeypatch.setattr(workflow_services, "installed_watcher_mode", lambda: None)
    monkeypatch.setattr(workflow_services, "ensure_watcher", fake_ensure_watcher)
    monkeypatch.setattr(workflow_services.Confirm, "ask", lambda *_args, **_kwargs: True)

    workflow_services.install_context_watcher(entry, requested_mode="auto", yes=False)

    assert calls == [("auto", False, False), ("auto", True, True)]

    calls.clear()
    monkeypatch.setattr(workflow_services.Confirm, "ask", lambda *_args, **_kwargs: False)
    with pytest.raises(ContextError, match="fallback"):
        workflow_services.install_context_watcher(entry, requested_mode="auto", yes=False)


def test_refresh_kubernetes_config_detection_handles_missing_and_compose(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    compose = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", compose)

    assert (
        _kubernetes_config_if_available(
            local_context("empty", root, ProviderType.YAML).model_copy(update={"config": ""})
        )
        is None
    )
    compose_entry = local_context("compose", root, ProviderType.YAML)
    assert _kubernetes_config_if_available(compose_entry) is None
