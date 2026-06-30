from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sftpwarden.config import (
    AuthConfig,
    ProjectConfig,
    ProviderConfig,
    ProviderType,
    SFTPWardenConfig,
    WatcherConfig,
    WatcherMode,
    config_as_json,
    default_project_config,
    load_config,
    provider_local_path,
    validation_error_to_config_error,
    write_config,
)
from sftpwarden.contexts import (
    ContextRegistry,
    load_registry,
    local_context,
    parse_remote_url,
    reconcile_registered_context,
    reconcile_registered_paths,
    remote_context,
    remove_context,
    resolve_context,
    save_registry,
    set_default_context,
)
from sftpwarden.utils.errors import ConfigError, ContextError
from sftpwarden.utils.validation import validate_octal_permissions, validate_relative_safe_path


def test_config_validation_edges(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="At least one authentication method"):
        AuthConfig(allow_password=False, allow_public_key=False)
    with pytest.raises(ValueError, match="relative"):
        validate_relative_safe_path("/absolute", field_name="provider.path")
    with pytest.raises(ValueError, match="octal"):
        validate_octal_permissions("999", field_name="mode")
    with pytest.raises(ValidationError, match="greater"):
        SFTPWardenConfig.model_validate(
            {"project": {"name": "dev"}, "uid_gid": {"start": 12000, "end": 12000}}
        )
    with pytest.raises(ValidationError, match="requires dsn"):
        ProviderConfig(type=ProviderType.POSTGRESQL)
    with pytest.raises(ValidationError, match="schema-qualified"):
        ProviderConfig(type=ProviderType.MYSQL, dsn="mysql://db/sftp", table="bad-name")
    with pytest.raises(ValidationError, match="SELECT or WITH"):
        ProviderConfig(type=ProviderType.MYSQL, dsn="mysql://db/sftp", query="delete from users")
    with pytest.raises(ValidationError, match="only supported for SQL"):
        ProviderConfig(type=ProviderType.YAML, dsn="mysql://db/sftp")
    with pytest.raises(ValidationError, match="watcher.image"):
        WatcherConfig(enabled=True, mode=WatcherMode.SYSTEMD, image="watcher:local")

    docker_watcher = WatcherConfig(enabled=True, mode=WatcherMode.DOCKER)
    assert docker_watcher.image is None

    source = tmp_path / "bad.yaml"
    source.write_text("project: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        SFTPWardenConfig.model_validate({"project": {}})
    error = validation_error_to_config_error(exc_info.value, source)
    assert isinstance(error, ConfigError)


def test_load_config_reports_missing_invalid_and_deprecated_keys(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(missing)

    deprecated = tmp_path / "deprecated.yaml"
    deprecated.write_text(
        yaml.safe_dump({"project": {"name": "dev"}, "server": {"container_port": 2222}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="container_port"):
        load_config(deprecated)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("project: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="project.name"):
        load_config(invalid)


def test_default_config_and_provider_path_helpers(tmp_path: Path) -> None:
    csv_config = default_project_config("dev", ProviderType.CSV)
    mysql_config = default_project_config(
        "dev", ProviderType.MYSQL, dsn="mysql://user:pass@db/sftp", table="custom_users"
    )
    runtime_config = default_project_config("dev")
    posix_host_config = default_project_config("dev")
    relative_config = SFTPWardenConfig(
        project=ProjectConfig(name="dev"),
        provider=ProviderConfig(type=ProviderType.YAML, path="nested/users.yaml"),
    )
    posix_host_config.provider.path = "/home/operator/external-users.yaml"

    assert csv_config.provider.path.endswith("users.csv")
    assert mysql_config.provider.table == "custom_users"
    assert provider_local_path(tmp_path, csv_config) == tmp_path / "users.csv"
    assert provider_local_path(tmp_path, runtime_config) == tmp_path / "users.yaml"
    assert provider_local_path(tmp_path, posix_host_config) == Path(
        "/home/operator/external-users.yaml"
    )
    assert provider_local_path(tmp_path, relative_config) == tmp_path / "nested" / "users.yaml"
    assert '"name": "dev"' in config_as_json(csv_config)


def test_context_registry_error_and_reconcile_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    with pytest.raises(ContextError, match="Invalid remote URL"):
        parse_remote_url("bad host")
    with pytest.raises(ContextError, match="Remote user is required"):
        remote_context(
            name="prod",
            provider=ProviderType.YAML,
            remote_url="example.com:/opt/sftpwarden",
            local_root=None,
            remote_root="~/sftpwarden",
            remote_only=True,
            ssh_key=None,
            critical=True,
        )
    with pytest.raises(ContextError, match="do not match"):
        remote_context(
            name="prod",
            provider=ProviderType.YAML,
            remote_url="deploy@example.com:/opt/sftpwarden",
            local_root=None,
            remote_root="~/sftpwarden",
            remote_only=True,
            ssh_key=None,
            critical=True,
            explicit_remote_root="/srv/sftpwarden",
        )

    registry_path = tmp_path / "contexts.toml"
    registry_path.write_text("not = [valid\n", encoding="utf-8")
    with pytest.raises(ContextError, match="Invalid context registry"):
        load_registry(registry_path)

    with pytest.raises(ContextError, match="Unknown context"):
        remove_context("missing")
    with pytest.raises(ContextError, match="Unknown context"):
        set_default_context("missing")


def test_reconcile_paths_and_context_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("manual")
    write_config(root / "sftpwarden.yaml", config)
    wrong_config = tmp_path / "old" / "sftpwarden.yaml"
    wrong_config.parent.mkdir()
    registry = ContextRegistry(
        default="dev",
        contexts={
            "dev": local_context("dev", root, ProviderType.CSV).model_copy(
                update={"config": str(wrong_config)}
            )
        },
    )

    reconciled = reconcile_registered_paths(registry, "dev")
    assert reconciled.config == str(root / "sftpwarden.yaml")
    renamed = reconcile_registered_context(registry, "dev")
    assert renamed.name == "manual"

    monkeypatch.setenv("SFTPWARDEN_CONTEXT", "manual")
    assert resolve_context().name == "manual"
    monkeypatch.delenv("SFTPWARDEN_CONTEXT")
    assert resolve_context(cwd=root).name == "manual"

    empty = tmp_path / "empty"
    empty.mkdir()
    save_registry(ContextRegistry())
    with pytest.raises(ContextError, match="No SFTPWarden context"):
        resolve_context(cwd=empty)


def test_context_reconcile_additional_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    write_config(root / "sftpwarden.yaml", default_project_config("dev", ProviderType.CSV))
    missing_config_root = tmp_path / "missing-config"
    missing_config_root.mkdir()
    registry = ContextRegistry(
        default="dev",
        contexts={
            "dev": local_context("dev", root, ProviderType.YAML),
            "other": local_context("other", tmp_path / "other", ProviderType.YAML),
            "missing": local_context("missing", missing_config_root, ProviderType.YAML),
        },
    )
    registry.contexts["missing"].config = str(missing_config_root / "missing.yaml")

    updated_provider = reconcile_registered_context(registry, "dev")
    same_missing = reconcile_registered_context(registry, "missing")
    save_registry(registry)
    removed = remove_context("dev")

    assert updated_provider.provider == ProviderType.CSV
    assert same_missing.name == "missing"
    assert removed.default in {"other", "missing"}

    registry = ContextRegistry(
        contexts={
            "dev": local_context("dev", root, ProviderType.CSV),
            "other": local_context("other", tmp_path / "other", ProviderType.YAML),
        }
    )
    write_config(root / "sftpwarden.yaml", default_project_config("other", ProviderType.CSV))
    with pytest.raises(ContextError, match="already exists"):
        reconcile_registered_context(registry, "dev")

    manual_root = tmp_path / "manual-root"
    stale_config = tmp_path / "stale" / "sftpwarden.yaml"
    manual_root.mkdir()
    stale_config.parent.mkdir()
    registry = ContextRegistry(
        contexts={
            "manual": local_context("manual", manual_root, ProviderType.YAML).model_copy(
                update={"config": str(stale_config)}
            )
        }
    )
    assert reconcile_registered_paths(registry, "manual").name == "manual"

    cwd_root = tmp_path / "cwd-project"
    cwd_root.mkdir()
    write_config(cwd_root / "sftpwarden.yaml", default_project_config("cwd-dev"))
    save_registry(ContextRegistry())
    assert resolve_context(cwd=cwd_root).name == "cwd-dev"


def test_reconcile_paths_reports_manual_inconsistent_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    config_path = other / "sftpwarden.yaml"
    write_config(config_path, default_project_config("dev"))
    registry = ContextRegistry(
        default="dev",
        contexts={
            "dev": local_context("dev", root, ProviderType.YAML).model_copy(
                update={"config": str(config_path)}
            )
        },
    )

    with pytest.raises(ContextError, match="inconsistent root/config paths"):
        reconcile_registered_paths(registry, "dev")
