from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from sftpwarden.config import default_project_config, load_config, write_config
from sftpwarden.contexts import remote_context
from sftpwarden.providers import provider_from_config
from sftpwarden.remote import deploy, environment
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.errors import ContextError


@pytest.fixture
def remote_project(tmp_path):
    config = default_project_config("env-delivery")
    write_config(tmp_path / "sftpwarden.yaml", config)
    provider_from_config(tmp_path, config).write(ProviderUsers(schema_version=2, users=[]))
    context = remote_context(
        name="env-delivery",
        provider=config.provider.type,
        remote_url="deploy@example.invalid:~/project 'quote; $literal",
        local_root=tmp_path,
        remote_root="~/project 'quote; $literal",
        remote_only=False,
        ssh_key=None,
        critical=False,
    )
    return context, config


def test_remote_deploy_only_delivers_environment_when_enabled(remote_project, monkeypatch):
    context, config = remote_project
    calls = []
    monkeypatch.setattr(environment, "deliver_environment", lambda entry: calls.append(entry.name))
    deploy.prepare_compose_deployment(context)
    assert not calls
    config = load_config(context.config)
    config.remote.include_env = True
    write_config(context.config, config)
    deploy.prepare_compose_deployment(context)
    assert calls == [context.name]


def test_environment_delivery_keeps_values_off_arguments(remote_project, tmp_path, monkeypatch):
    context, _config = remote_project
    content = b"DATABASE_DSN='postgresql://user:synthetic$pass;@db/test'\n"
    (tmp_path / ".env").write_bytes(content)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(environment.subprocess, "run", run)
    environment.deliver_environment(context)
    arguments, options = calls[0]
    assert "synthetic$pass" not in str(arguments)
    assert options["input"] == content
    assert options["timeout"] == 60
    assert options["stderr"] == subprocess.DEVNULL
    assert 'root="$HOME"/' in arguments[-1]
    assert 'chmod 600 "$temporary"' in arguments[-1]
    assert 'mv -f -- "$temporary" .env' in arguments[-1]


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "too-large", "invalid-utf8"])
def test_invalid_environment_is_rejected_before_connecting(
    kind, remote_project, tmp_path, monkeypatch
):
    context, _config = remote_project
    path = tmp_path / ".env"
    if kind == "symlink":
        other = tmp_path / "other"
        other.write_text("private=value")
        path.symlink_to(other)
    elif kind == "directory":
        path.mkdir()
    elif kind == "too-large":
        path.write_bytes(b"x" * (1024 * 1024 + 1))
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("SSH attempted for invalid input"),
    )
    with pytest.raises(ContextError, match="stable, readable"):
        environment.deliver_environment(context)
