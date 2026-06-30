from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.services.cli_workflows as workflow_services
from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import (
    remote_context,
    remote_url_from_parts,
)

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


def test_cli_workflow_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert (
        remote_url_from_parts(
            host="example.com", remote_root="/opt/sftpwarden", remote_user="deploy"
        )
        == "deploy@example.com:/opt/sftpwarden"
    )
    assert (
        remote_url_from_parts(host="example.com", remote_root="/opt/sftpwarden", remote_user=None)
        == "example.com:/opt/sftpwarden"
    )

    config = default_project_config("dev")
    root = tmp_path / "project"
    root.mkdir()
    write_config(root / "sftpwarden.yaml", config)
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    calls: list[tuple[str | None, bool, bool]] = []
    monkeypatch.setattr(workflow_services, "installed_watcher_mode", lambda: None)
    monkeypatch.setattr(
        workflow_services,
        "ensure_watcher",
        lambda *, requested_mode, yes, allow_docker_fallback: (
            calls.append((requested_mode, yes, allow_docker_fallback)) or "installed"
        ),
    )

    workflow_services.install_context_watcher(entry, requested_mode="systemd", yes=True)

    assert calls == [("systemd", True, True)]


def test_cli_workflow_watcher_replacement_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = default_project_config("prod")
    root = tmp_path / "project"
    root.mkdir()
    write_config(root / "sftpwarden.yaml", config)
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    calls: list[tuple[str | None, bool, bool]] = []
    monkeypatch.setattr(
        workflow_services,
        "installed_watcher_mode",
        lambda: type("Mode", (), {"value": "systemd"})(),
    )
    monkeypatch.setattr(workflow_services.Confirm, "ask", lambda *_args, **_kwargs: False)
    workflow_services.install_context_watcher(entry, requested_mode="docker", yes=False)
    monkeypatch.setattr(workflow_services.Confirm, "ask", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        workflow_services,
        "ensure_watcher",
        lambda *, requested_mode, yes, allow_docker_fallback: (
            calls.append((requested_mode, yes, allow_docker_fallback)) or "replaced"
        ),
    )
    workflow_services.install_context_watcher(entry, requested_mode="docker", yes=False)
    workflow_services.install_context_watcher(
        entry.model_copy(update={"watcher_required": False}),
        requested_mode="docker",
        yes=True,
    )

    assert calls == [("docker", True, True)]
