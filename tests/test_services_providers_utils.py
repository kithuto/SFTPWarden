from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import sftpwarden.providers.registry as provider_registry
import sftpwarden.services.cli_workflows as workflow_services
import sftpwarden.services.users as user_services
import sftpwarden.system.commands as command_services
from sftpwarden.cli import app
from sftpwarden.config import ProviderConfig, ProviderType, default_project_config, write_config
from sftpwarden.config.global_config import load_global_config, resolve_provider, save_global_config
from sftpwarden.contexts import (
    ContextRegistry,
    remote_context,
    remote_url_from_parts,
    save_registry,
)
from sftpwarden.providers import (
    ProviderUsers,
    empty_provider_text,
    load_users,
    provider_for_config,
    provider_for_project,
    provider_local,
    save_users,
)
from sftpwarden.providers import mutations as provider_mutations
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import provider_class, registered_providers
from sftpwarden.security.passwords import hash_password, resolve_password_hash
from sftpwarden.services.users import UserService
from sftpwarden.users import SFTPUser
from sftpwarden.users.service import remove_user
from sftpwarden.utils.collections import unique_items
from sftpwarden.utils.console import print_warning
from sftpwarden.utils.dotted import format_value, get_dotted, parse_cli_value, set_dotted
from sftpwarden.utils.dsn import sql_default_port, sql_dsn_scheme
from sftpwarden.utils.errors import ConfigError, ProviderError, RuntimeError
from sftpwarden.utils.paths import (
    app_home,
    contexts_path,
    ensure_parent,
    global_config_path,
    project_config_path,
)

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


def test_collection_and_dsn_utilities_are_stable() -> None:
    assert unique_items(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]
    assert sql_dsn_scheme(ProviderType.MYSQL) == "mysql"
    assert sql_dsn_scheme(ProviderType.POSTGRESQL) == "postgresql"
    assert sql_default_port(ProviderType.MYSQL) == 3306
    assert sql_default_port(ProviderType.POSTGRESQL) == 5432


def test_warning_output_uses_standard_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    print_warning("check this")

    assert "Warning" in capsys.readouterr().out


def test_provider_wrapper_functions_round_trip_yaml(tmp_path: Path) -> None:
    path = tmp_path / "users.yaml"
    users = ProviderUsers(users=[SFTPUser(username="alice", password_hash=TEST_HASH)])

    save_users(ProviderType.YAML, path, users)
    loaded = load_users(ProviderType.YAML, path)
    config = default_project_config("dev")
    project_provider = provider_for_project(tmp_path, config)
    direct_provider = provider_for_config(ProviderConfig(type=ProviderType.YAML), path)
    local = provider_local(tmp_path, config)

    assert loaded.users[0].username == "alice"
    assert project_provider.path == tmp_path / "users.yaml"
    assert direct_provider.path == path
    assert local.path == tmp_path / "users.yaml"
    assert ProviderType.YAML in registered_providers()


def test_provider_registry_reports_unknown_registered_provider() -> None:
    class UnregisteredProvider:
        provider_type = ProviderType.YAML

    with pytest.raises(ValueError):
        provider_class("unknown")


def test_file_provider_requires_path_and_empty_file_round_trips(tmp_path: Path) -> None:
    provider = provider_for_config(ProviderConfig(type=ProviderType.YAML))
    with pytest.raises(ProviderError, match="requires a path"):
        provider.read()
    with pytest.raises(ProviderError, match="Provider file not found"):
        provider_for_config(
            ProviderConfig(type=ProviderType.YAML), tmp_path / "missing.yaml"
        ).read()

    path = tmp_path / "empty.yaml"
    path.write_text(empty_provider_text(ProviderType.YAML), encoding="utf-8")
    assert load_users(ProviderType.YAML, path).users == []
    nested = tmp_path / "nested" / "users.yaml"
    provider_for_config(ProviderConfig(type=ProviderType.YAML), nested).write(
        ProviderUsers(users=[])
    )
    assert nested.exists()

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("users:\n  - username: BadUser\n", encoding="utf-8")
    with pytest.raises(ProviderError, match="Invalid YAML"):
        load_users(ProviderType.YAML, bad_yaml)

    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(
        "username,public_keys,password_hash,uid,gid,upload_dir,comment,disabled\n"
        f"alice,,{TEST_HASH},,,,false\n"
        f"alice,,{TEST_HASH},,,,false\n",
        encoding="utf-8",
    )
    with pytest.raises(ProviderError, match="Invalid CSV"):
        load_users(ProviderType.CSV, bad_csv)


def test_base_provider_defaults_and_registry_errors() -> None:
    class MinimalProvider(BaseProvider):
        provider_type = ProviderType.YAML

        @classmethod
        def empty_text(cls) -> str:
            return super().empty_text()

        def read(self) -> ProviderUsers:
            return super().read()

        def write(self, users: ProviderUsers) -> None:
            super().write(users)

    provider = MinimalProvider(ProviderConfig(type=ProviderType.YAML))

    with pytest.raises(NotImplementedError):
        MinimalProvider.empty_text()
    with pytest.raises(NotImplementedError):
        provider.read()
    with pytest.raises(NotImplementedError):
        provider.write(ProviderUsers(users=[]))
    original = provider_registry._PROVIDERS.copy()
    try:
        provider_registry._PROVIDERS.pop(ProviderType.YAML, None)
        with pytest.raises(ProviderError, match="not registered"):
            provider_class("yaml")
    finally:
        provider_registry._PROVIDERS.clear()
        provider_registry._PROVIDERS.update(original)
    assert provider_mutations.users_fingerprint(ProviderUsers(users=[]))


def test_password_helpers_validate_inputs() -> None:
    with pytest.raises(ProviderError, match="empty"):
        hash_password("")
    with pytest.raises(ProviderError, match="at least 8"):
        hash_password("short")
    with pytest.raises(ProviderError, match="whitespace"):
        resolve_password_hash(password=None, password_hash=f" {TEST_HASH}")
    assert resolve_password_hash(password=None, password_hash=TEST_HASH) == TEST_HASH


def test_user_model_validation_edges() -> None:
    with pytest.raises(ValidationError, match="requires at least one"):
        SFTPUser(username="empty")
    with pytest.raises(ValidationError, match="Username"):
        SFTPUser(username="BadUser", password_hash=TEST_HASH)
    with pytest.raises(ValidationError, match="SSH public key"):
        SFTPUser(username="alice", public_keys=["not-a-key"])
    with pytest.raises(ValidationError, match="plaintext"):
        SFTPUser(username="alice", password_hash="plaintext")  # noqa: S106
    with pytest.raises(ValidationError, match="duplicate usernames"):
        ProviderUsers(
            users=[
                SFTPUser(username="alice", password_hash=TEST_HASH),
                SFTPUser(username="alice", password_hash=TEST_HASH),
            ]
        )
    with pytest.raises(ValidationError, match="duplicate explicit UIDs"):
        ProviderUsers(
            users=[
                SFTPUser(username="alice", uid=12000, password_hash=TEST_HASH),
                SFTPUser(username="bob", uid=12000, password_hash=TEST_HASH),
            ]
        )
    with pytest.raises(ProviderError, match="Unknown user"):
        remove_user(ProviderUsers(users=[]), "missing")


def test_dotted_and_path_utilities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"server": {"port": 2222}, "flags": {"enabled": True}, "items": [1, 2]}
    set_dotted(data, "server.port", parse_cli_value("2200"))

    assert get_dotted(data, "server.port") == 2200
    assert format_value(True) == "true"
    assert format_value([1, 2]) == "- 1\n- 2"
    with pytest.raises(ConfigError, match="Unknown configuration path"):
        get_dotted(data, "missing.value")
    with pytest.raises(ConfigError, match="Unknown configuration path"):
        set_dotted(data, "server.missing", 1)

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    target = tmp_path / "nested" / "file.txt"
    ensure_parent(target)
    assert target.parent.is_dir()
    assert app_home() == tmp_path / "home"
    assert global_config_path() == tmp_path / "home" / "config.toml"
    assert contexts_path() == tmp_path / "home" / "contexts.toml"
    assert project_config_path(tmp_path / "project") == tmp_path / "project" / "sftpwarden.yaml"


def test_global_config_and_command_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad_config = tmp_path / "bad.toml"
    bad_config.write_text("not = [valid\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid global config"):
        load_global_config(bad_config)

    monkeypatch.setenv("SFTPWARDEN_DEFAULT_PROVIDER", "csv")
    assert resolve_provider() == ProviderType.CSV
    monkeypatch.delenv("SFTPWARDEN_DEFAULT_PROVIDER")
    saved = tmp_path / "config.toml"
    config = load_global_config()
    config.default_provider = ProviderType.CSV
    save_global_config(config, saved)
    assert load_global_config(saved).default_provider == ProviderType.CSV

    def missing_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("missing-binary")

    monkeypatch.setattr(command_services.subprocess, "run", missing_run)
    result = command_services.run(["definitely-not-a-real-sftpwarden-binary"])
    assert result.returncode == 127
    monkeypatch.setattr(
        command_services,
        "run",
        lambda *_args, **_kwargs: command_services.CommandResult(
            args=["bad"], returncode=2, stdout="", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="failed"):
        command_services.run_checked(
            ["definitely-not-a-real-sftpwarden-binary"],
            error_type=RuntimeError,
            message="failed",
            fallback_suggestion="fallback",
        )


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
    calls: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(workflow_services, "installed_watcher_mode", lambda: None)
    monkeypatch.setattr(
        workflow_services,
        "ensure_watcher",
        lambda *, requested_mode, yes: calls.append((requested_mode, yes)) or "installed",
    )

    workflow_services.install_context_watcher(entry, requested_mode="systemd", yes=True)

    assert calls == [("systemd", True)]


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
    calls: list[tuple[str | None, bool]] = []
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
        lambda *, requested_mode, yes: calls.append((requested_mode, yes)) or "replaced",
    )
    workflow_services.install_context_watcher(entry, requested_mode="docker", yes=False)
    workflow_services.install_context_watcher(
        entry.model_copy(update={"watcher_required": False}),
        requested_mode="docker",
        yes=True,
    )

    assert calls == [("docker", True)]


def test_user_service_delete_file_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    runner = CliRunner()
    result = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output
    service = UserService(context_name="dev")

    assert service.delete_user_files("missing") == "No data directory found for missing."
    data_dir = root / "data" / "alice"
    data_dir.mkdir(parents=True)
    (data_dir / "file.txt").write_text("data", encoding="utf-8")
    assert "Deleted data directory" in service.delete_user_files("alice")
    assert not data_dir.exists()
    with pytest.raises(RuntimeError, match="Invalid username"):
        service.delete_user_files("../bad")


def test_user_service_remote_delete_uses_safe_remote_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("prod")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text("users: []\n", encoding="utf-8")
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
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    commands: list[list[str]] = []
    monkeypatch.setattr(
        user_services,
        "run_checked",
        lambda command, **_kwargs: commands.append(command),
    )

    message = UserService(context_name="prod").delete_user_files("alice")

    assert message == "Deleted remote data directory for alice: /opt/sftpwarden/data/alice"
    assert commands
    assert "rm -rf -- /opt/sftpwarden/data/alice" in commands[0][-1]


def test_user_service_remote_delete_requires_remote_settings(tmp_path: Path) -> None:
    entry = remote_context(
        name="broken",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    entry.remote = None
    service = object.__new__(UserService)
    service.entry = entry

    with pytest.raises(RuntimeError, match="missing remote settings"):
        service.delete_user_files("alice")
