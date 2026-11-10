from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import local_context, remote_context
from sftpwarden.refresh.core import docker_compose_command, refresh_context
from sftpwarden.services import health
from sftpwarden.system.commands import CommandResult


def fingerprints(arguments: list[str]) -> tuple[str, str]:
    return (
        arguments[arguments.index("--desired-provider-fingerprint") + 1],
        arguments[arguments.index("--desired-config-fingerprint") + 1],
    )


def test_refresh_carries_host_desired_fingerprints_without_credentials(tmp_path: Path) -> None:
    config = default_project_config(
        "desired", ProviderType.POSTGRESQL, dsn="postgresql://test:synthetic-secret@db/users"
    )
    path = tmp_path / "sftpwarden.yaml"
    write_config(path, config)
    context = local_context("desired", tmp_path, ProviderType.POSTGRESQL)
    command = docker_compose_command(context)
    provider, complete = fingerprints(command)
    assert len(provider) == len(complete) == 64
    assert "synthetic-secret" not in " ".join(command)
    config.auth.allow_password = False
    write_config(path, config)
    updated = fingerprints(docker_compose_command(context))
    assert updated[0] == provider and updated[1] != complete
    config.provider.table = "changed_users"
    write_config(path, config)
    assert fingerprints(docker_compose_command(context))[0] != provider


def test_remote_local_sync_refresh_carries_local_desired_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = default_project_config("remote")
    write_config(tmp_path / "sftpwarden.yaml", config)
    context = remote_context(
        name="remote",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/runtime",
        local_root=tmp_path,
        remote_root="/runtime",
        remote_only=False,
        ssh_key=None,
        critical=False,
    )
    rendered = refresh_context(context, dry_run=True)
    assert "--desired-provider-fingerprint" in rendered
    assert "--desired-config-fingerprint" in rendered


def test_health_builds_an_observational_command_without_refresh_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = default_project_config("health")
    write_config(tmp_path / "sftpwarden.yaml", config)
    context = local_context("health", tmp_path, ProviderType.YAML)
    captured = []

    def run(arguments, **kwargs):
        captured.extend(arguments)
        return CommandResult(arguments, 0, "healthy", "")

    monkeypatch.setattr(health, "run", run)
    assert health.runtime_health_from_context(context)[0].status == "pass"
    assert captured[captured.index("runtime") + 1] == "health"
    assert "refresh" not in captured
    assert not any(argument.startswith("--desired-") for argument in captured)
