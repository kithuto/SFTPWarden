from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import local_context, remote_context
from sftpwarden.refresh.core import docker_compose_command


@pytest.mark.parametrize("operation", ["refresh", "health"])
def test_local_runtime_commands_use_configured_compose_file(tmp_path: Path, operation: str) -> None:
    config = default_project_config("custom")
    config.docker.compose_file = "runtime compose.yaml"
    write_config(tmp_path / "sftpwarden.yaml", config)
    context = local_context("custom", tmp_path, ProviderType.YAML)
    command = docker_compose_command(context, runtime_command=operation)
    assert command[:4] == ["docker", "compose", "-f", "runtime compose.yaml"]
    assert command[command.index("runtime") + 1] == operation
    assert ("--desired-config-fingerprint" in command) is (operation == "refresh")


def test_remote_context_retains_its_explicit_compose_file(tmp_path: Path) -> None:
    config = default_project_config("remote")
    config.docker.compose_file = "desired.yaml"
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
    assert context.remote is not None
    context.remote.compose_file = "on host.yaml"
    assert docker_compose_command(context)[3] == "on host.yaml"


def test_context_without_local_config_retains_default_command(tmp_path: Path) -> None:
    context = local_context("missing", tmp_path, ProviderType.YAML)
    command = docker_compose_command(context)
    assert command[3] == "docker-compose.yml"
    assert "--desired-config-fingerprint" not in command
