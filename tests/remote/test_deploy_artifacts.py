from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import remote_context
from sftpwarden.remote.deploy import deploy_plan, prepare_compose_deployment
from sftpwarden.render.compose import write_compose
from sftpwarden.utils import persistence
from sftpwarden.utils.errors import ContextError


@pytest.mark.parametrize("platform", ["Linux", "Darwin", "Windows"])
def test_remote_sync_delivers_the_compose_file_selected_by_the_context(
    tmp_path, monkeypatch, platform
):
    import sftpwarden.remote.deploy as deploy_module

    monkeypatch.setattr(deploy_module, "system_is", lambda name: name == platform)
    config = default_project_config("remote-artifacts")
    config.docker.compose_file = "local desired.yaml"
    write_config(tmp_path / "sftpwarden.yaml", config)
    (tmp_path / "users.yaml").write_text("schema_version: 2\nusers: []\n")
    context = remote_context(
        name="remote-artifacts",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/srv/project with spaces",
        local_root=tmp_path,
        remote_root="/srv/project with spaces",
        remote_only=False,
        ssh_key=None,
        critical=False,
    )
    context.remote.compose_file = "remote runtime.yaml"
    prepare_compose_deployment(context)
    generated = tmp_path / context.remote.compose_file
    assert generated.is_file()
    commands = deploy_plan(context).commands
    transfers = [command for command in commands if command[0] in {"rsync", "scp"}]
    assert any(
        any(part.endswith("/remote runtime.yaml") for part in command) for command in transfers
    )
    remote_up = next(command[-1] for command in commands if " up -d" in command[-1])
    assert "-f 'remote runtime.yaml'" in remote_up
    assert "local desired.yaml" not in remote_up
    assert all(command[0] == ("rsync" if platform == "Linux" else "scp") for command in transfers)


def test_remote_only_deploy_recreates_and_waits_for_runtime_readiness():
    context = remote_context(
        name="remote-only",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/srv/remote",
        local_root=None,
        remote_root="/srv/remote",
        remote_only=True,
        ssh_key=None,
        critical=False,
    )
    up = next(command[-1] for command in deploy_plan(context).commands if " up -d" in command[-1])
    assert "--force-recreate" in up
    assert "--wait --wait-timeout 90" in up


def test_failed_compose_publication_preserves_previous_artifact(tmp_path, monkeypatch):
    config = default_project_config("atomic-compose")
    target = write_compose(config, tmp_path)
    before = target.read_bytes()
    config.server.port += 1

    def fail_replace(source, destination):
        assert Path(destination) == target
        raise OSError("simulated publication failure")

    monkeypatch.setattr(persistence.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publication failure"):
        write_compose(config, tmp_path)
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "filename",
    [
        "../outside.yaml",
        "/outside.yaml",
        "C:\\outside.yaml",
        "subdir/runtime.yaml",
        "",
        "sftpwarden.yaml",
        "users.yaml",
        ".env",
        "provider",
    ],
)
def test_invalid_remote_artifact_is_rejected_before_provider_migration(tmp_path, filename):
    config = default_project_config("remote-artifacts")
    source = tmp_path / "sftpwarden.yaml"
    write_config(source, config)
    before = source.read_bytes()
    provider = tmp_path / "users.yaml"
    provider.write_text("schema_version: 2\nusers: []\n")
    environment_file = tmp_path / ".env"
    environment_file.write_text("EXISTING_VALUE=keep\n")
    context = remote_context(
        name="remote-artifacts",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/srv/project",
        local_root=tmp_path,
        remote_root="/srv/project",
        remote_only=False,
        ssh_key=None,
        critical=False,
    )
    context.remote.compose_file = filename
    with pytest.raises(ContextError, match="Compose"):
        prepare_compose_deployment(context)
    assert source.read_bytes() == before
    assert provider.read_text() == "schema_version: 2\nusers: []\n"
    assert not (tmp_path / "provider").exists()
    assert environment_file.read_text() == "EXISTING_VALUE=keep\n"
