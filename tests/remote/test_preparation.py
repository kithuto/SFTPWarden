import sys
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.contexts import remote_context
from sftpwarden.providers import provider_from_config
from sftpwarden.remote import preparation
from sftpwarden.remote.deploy import deploy_plan, remote_path_expression
from sftpwarden.remote.preparation import (
    PREPARATION_FILE,
    apply_preparation,
    bootstrap_preparation,
    finish_preparation,
)
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.persistence import migration_marker, pending_deploy_marker

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFMsDFmvcbuEKKQ2nRx6M4jxyJuS9KoOesN7GyrXw+d+"


@pytest.mark.parametrize(
    "value", ["", ".", "../outside", "/outside", "dir\\file", "bad\nfile", "bad\x00file"]
)
def test_remote_config_path_is_bounded_before_opening(value):
    with pytest.raises(ProviderError):
        preparation.validate_relative_config(value)


@pytest.mark.parametrize("phase", ["bootstrap", "apply", "complete"])
@pytest.mark.parametrize("failure", [False, True])
def test_preparation_entrypoint_routes_phase_and_redacts_errors(monkeypatch, phase, failure):
    arguments = [
        "prepare",
        phase,
        "--root",
        "project",
        "--config",
        "sftpwarden.yaml",
        "--compose-file",
        "runtime.yaml",
        "--host-root",
        "/srv/project",
        "--image",
        "candidate",
        "--owner",
        "1000:1000",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    calls = []

    def run(root, **kwargs):
        calls.append((root, kwargs))
        if failure:
            raise OSError("private-sentinel")

    name = {
        "bootstrap": "bootstrap_preparation",
        "apply": "apply_preparation",
        "complete": "finish_preparation",
    }[phase]
    monkeypatch.setattr(preparation, name, run)
    if failure:
        with pytest.raises(SystemExit, match="preparation failed") as caught:
            preparation.main()
        assert "private-sentinel" not in str(caught.value)
    else:
        preparation.main()
    assert calls[0][0] == Path("project")
    assert calls[0][1]["compose_file"] == "runtime.yaml"


def test_bootstrap_entrypoint_requires_all_host_parameters(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare",
            "bootstrap",
            "--root",
            "project",
            "--config",
            "config.yaml",
            "--compose-file",
            "runtime.yaml",
        ],
    )
    with pytest.raises(SystemExit) as caught:
        preparation.main()
    assert caught.value.code == 2


def test_remote_only_plan_prepares_before_recreating_and_completes_after_readiness():
    context = remote_context(
        name="remote",
        provider=ProviderType.SQLITE,
        remote_url="deploy@example.invalid:/srv/project with spaces",
        local_root=None,
        remote_root="/srv/project with spaces",
        remote_only=True,
        ssh_key=None,
        critical=False,
    )
    context.remote.compose_file = "remote runtime.yml"
    commands = [item[-1] for item in deploy_plan(context).commands]
    bootstrap = next(index for index, item in enumerate(commands) if "bootstrap" in item)
    prepare = next(index for index, item in enumerate(commands) if "run --rm -T prepare" in item)
    up = next(index for index, item in enumerate(commands) if "up -d" in item)
    complete = next(index for index, item in enumerate(commands) if "complete --root" in item)
    assert bootstrap < prepare < up < complete
    assert "--force-recreate --wait --wait-timeout 90" in commands[up]
    assert "--compose-file=remote runtime.yml" in commands[bootstrap]


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_remote_preparation_migrates_then_releases_edits_only_on_completion(tmp_path, kind):
    path = tmp_path / "custom config.yaml"
    config = default_project_config("remote", kind, user_schema=1)
    write_config(path, config)
    original = provider_from_config(tmp_path, config)
    users = ProviderUsers(schema_version=1, users=[SFTPUser(username="alice", public_keys=[KEY])])
    original.write(users)
    original_path = original.path
    previous_compose = b"services: {}\n"
    (tmp_path / "selected compose.yml").write_bytes(previous_compose)
    bootstrap_preparation(
        tmp_path,
        relative_config=path.name,
        compose_file="selected compose.yml",
        host_root="/srv/remote",
        image="example.invalid/sftpwarden:1.4.0",
        owner="1000:1000",
    )
    assert not (tmp_path / "provider").exists()
    assert original.read() == users
    apply_preparation(tmp_path, relative_config=path.name, compose_file="selected compose.yml")
    updated = load_config(path)
    current = provider_from_config(tmp_path, updated)
    assert current.read() == users
    assert migration_marker(original_path).exists()
    assert pending_deploy_marker(current.path).exists()
    with pytest.raises(ProviderError, match="successful deploy"):
        current.write(users)
    rendered = yaml.safe_load((tmp_path / "selected compose.yml").read_text())
    volumes = rendered["services"]["sftpwarden"]["volumes"]
    assert volumes[0]["source"] == "./custom config.yaml"
    assert any(str(item).startswith("./provider:") for item in volumes)
    assert next((tmp_path / ".sftpwarden" / "backups").glob("compose-*.yaml")).read_bytes() == (
        previous_compose
    )
    finish_preparation(tmp_path, relative_config=path.name, compose_file="selected compose.yml")
    current.write(users)
    assert not pending_deploy_marker(current.path).exists()
    assert not (tmp_path / PREPARATION_FILE).exists()


def test_bootstrap_forwards_only_referenced_env_without_reading_database(tmp_path, monkeypatch):
    config = default_project_config(
        "remote",
        ProviderType.POSTGRESQL,
        dsn="${DATABASE_URL}",
        user_schema=2,
    )
    write_config(tmp_path / "sftpwarden.yaml", config)
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret:private@private.invalid/db")
    bootstrap_preparation(
        tmp_path,
        relative_config="sftpwarden.yaml",
        compose_file="compose.yml",
        host_root="/srv/project",
        image="candidate:1.4.0",
        owner="1000:1000",
    )
    text = (tmp_path / PREPARATION_FILE).read_text()
    assert "secret" not in text and "private.invalid" not in text
    model = yaml.safe_load(text)
    assert list(model["services"]["prepare"]["environment"]) == ["DATABASE_URL"]
    assert model["services"]["prepare"]["network_mode"] == "bridge"


@pytest.mark.parametrize("filename", ["../escape.yml", "users.yaml", ".env", "sftpwarden.yaml"])
def test_remote_preparation_rejects_artifact_conflicts_before_any_changes(tmp_path, filename):
    path = tmp_path / "sftpwarden.yaml"
    write_config(path, default_project_config("remote"))
    before = path.read_bytes()
    with pytest.raises(ProviderError, match="Compose"):
        bootstrap_preparation(
            tmp_path,
            relative_config=path.name,
            compose_file=filename,
            host_root="/srv/project",
            image="candidate:1.4.0",
            owner="1000:1000",
        )
    assert path.read_bytes() == before
    assert not (tmp_path / ".sftpwarden").exists()


def test_remote_preparation_keeps_leading_dash_filenames_as_option_values(tmp_path):
    path = tmp_path / "-config.yaml"
    write_config(path, default_project_config("remote"))
    bootstrap_preparation(
        tmp_path,
        relative_config=path.name,
        compose_file="-runtime.yml",
        host_root="/srv/project",
        image="candidate:1.4.0",
        owner="1000:1000",
    )
    helper = yaml.safe_load((tmp_path / PREPARATION_FILE).read_text())
    command = helper["services"]["prepare"]["entrypoint"]
    assert "--config=-config.yaml" in command
    assert "--compose-file=-runtime.yml" in command


@pytest.mark.parametrize("path", ["/srv/a\nunsafe", "/srv/a\runsafe", "/srv/a\0unsafe"])
def test_remote_path_rejects_control_characters(path):
    from sftpwarden.utils.errors import ContextError

    with pytest.raises(ContextError, match="control characters"):
        remote_path_expression(path)


def test_remote_home_prefix_expands_without_interpreting_path_contents():
    assert remote_path_expression("~/project; command") == "\"$HOME\"/'project; command'"
