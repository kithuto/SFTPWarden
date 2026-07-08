from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from sftpwarden.cli_commands.context import CONTEXT_FIELD_COMMANDS
from sftpwarden.utils.constants import PROJECT_CONFIG_PATHS

from .conftest import ReleaseCli, assert_ok

CONFIG_MUTATION_VALUES: dict[str, tuple[str, Any]] = {
    "version": ("1", 1),
    "project.name": ("release-config", "release-config"),
    "project.description": ("Release validation project", "Release validation project"),
    "server.host": ("127.0.0.1", "127.0.0.1"),
    "server.port": ("2244", 2244),
    "server.data_dir": ("/srv/sftpwarden/data", "/srv/sftpwarden/data"),
    "server.host_keys_dir": ("/srv/sftpwarden/host_keys", "/srv/sftpwarden/host_keys"),
    "server.state_dir": ("/srv/sftpwarden/state", "/srv/sftpwarden/state"),
    "server.group": ("sftp_users", "sftp_users"),
    "sync.enabled": ("false", False),
    "sync.interval_seconds": ("15", 15),
    "sync.apply_on_startup": ("false", False),
    "sync.disable_missing_users": ("false", False),
    "sync.delete_missing_user_data": ("true", True),
    "auth.allow_public_key": ("false", False),
    "auth.allow_password": ("false", False),
    "auth.recommended": ("public_key", "public_key"),
    "auth.password_hash_scheme": ("sha512crypt", "sha512crypt"),
    "isolation.mode": ("chroot", "chroot"),
    "isolation.upload_dir": ("incoming", "incoming"),
    "isolation.root_owner": ("root", "root"),
    "isolation.root_group": ("root", "root"),
    "isolation.root_permissions": ("755", "755"),
    "isolation.upload_permissions": ("750", "750"),
    "uid_gid.mode": ("auto", "auto"),
    "uid_gid.start": ("12000", 12000),
    "uid_gid.end": ("65000", 65000),
    "uid_gid.preserve_existing": ("false", False),
    "provider.type": ("csv", "csv"),
    "provider.path": ("/etc/sftpwarden/release-users.yaml", "/etc/sftpwarden/release-users.yaml"),
    "provider.dsn": (
        "mysql://sftpwarden:sftpwarden@127.0.0.1:3306/sftpwarden",
        "mysql://sftpwarden:sftpwarden@127.0.0.1:3306/sftpwarden",
    ),
    "provider.query": (
        "SELECT username, password_hash, public_key, disabled, comment, uid, gid FROM sftp_users",
        "SELECT username, password_hash, public_key, disabled, comment, uid, gid FROM sftp_users",
    ),
    "provider.table": ("release_users", "release_users"),
    "provider.collection": ("release_users", "release_users"),
    "provider.user_schema": ("2", 2),
    "logging.level": ("debug", "debug"),
    "logging.format": ("text", "text"),
    "healthcheck.interval_seconds": ("7", 7),
    "healthcheck.timeout_seconds": ("3", 3),
    "healthcheck.retries": ("5", 5),
    "healthcheck.start_period_seconds": ("2", 2),
    "docker.image": ("sftpwarden:release-validation", "sftpwarden:release-validation"),
    "docker.container_name": ("sftpwarden-release-validation", "sftpwarden-release-validation"),
    "docker.restart": ("always", "always"),
    "docker.compose_file": ("compose.release.yml", "compose.release.yml"),
    "deploy.target": ("kubernetes", "kubernetes"),
    "kubernetes.mode": ("helm", "helm"),
    "kubernetes.namespace": ("release-validation", "release-validation"),
    "kubernetes.release": ("sftpwarden-release", "sftpwarden-release"),
    "kubernetes.kube_context": ("kind-release", "kind-release"),
    "kubernetes.service_type": ("NodePort", "NodePort"),
    "kubernetes.storage_class": ("fast", "fast"),
    "kubernetes.data_storage_size": ("2Gi", "2Gi"),
    "kubernetes.startup_probe.period_seconds": ("6", 6),
    "kubernetes.startup_probe.timeout_seconds": ("4", 4),
    "kubernetes.startup_probe.failure_threshold": ("12", 12),
    "kubernetes.readiness_probe.period_seconds": ("8", 8),
    "kubernetes.readiness_probe.timeout_seconds": ("4", 4),
    "kubernetes.readiness_probe.failure_threshold": ("4", 4),
    "kubernetes.liveness_probe.period_seconds": ("20", 20),
    "kubernetes.liveness_probe.timeout_seconds": ("4", 4),
    "kubernetes.liveness_probe.failure_threshold": ("4", 4),
    "kubernetes.replicas": ("1", 1),
    "remote.enabled": ("true", True),
    "remote.storage": ("remote-only", "remote-only"),
    "remote.host": ("release.example.com", "release.example.com"),
    "remote.user": ("deploy", "deploy"),
    "remote.port": ("2222", 2222),
    "remote.remote_root": ("/srv/sftpwarden-release", "/srv/sftpwarden-release"),
    "remote.remote_config": (
        "/srv/sftpwarden-release/sftpwarden.yaml",
        "/srv/sftpwarden-release/sftpwarden.yaml",
    ),
    "remote.ssh_key": (
        "/home/deploy/.ssh/sftpwarden_release",
        "/home/deploy/.ssh/sftpwarden_release",
    ),
    "remote.delete_extra_files": ("true", True),
    "remote.include_env": ("true", True),
    "watcher.enabled": ("true", True),
    "watcher.mode": ("docker", "docker"),
    "watcher.image": (
        "sftpwarden-watcher:release-validation",
        "sftpwarden-watcher:release-validation",
    ),
}


@pytest.mark.release_validation
def test_config_mutation_matrix_tracks_every_registered_project_path() -> None:
    """Every code-registered config path should have a real mutation value."""
    assert set(CONFIG_MUTATION_VALUES) == set(PROJECT_CONFIG_PATHS)


@pytest.mark.release_validation
@pytest.mark.parametrize("path", PROJECT_CONFIG_PATHS)
def test_config_dynamic_commands_persist_real_file_changes(
    cli: ReleaseCli,
    tmp_path: Path,
    path: str,
) -> None:
    """Each dynamic config command should persist the promised value in YAML."""
    root = tmp_path / path.replace(".", "_")
    init_args: list[str | Path] = ["init", f"cfg-{path.replace('.', '-')}", "--root", root, "--yes"]
    if path == "provider.user_schema":
        init_args.extend(["--user-schema", "1"])
    assert_ok(cli.run(*init_args))
    config_path = root / "sftpwarden.yaml"
    raw_value, expected = CONFIG_MUTATION_VALUES[path]
    _prepare_config_for_path(config_path, path)

    update_args: list[str | Path] = ["config", path, raw_value, "--config", config_path]
    if path == "provider.user_schema":
        update_args.append("--yes")
    update = cli.run(*update_args)
    read_back = cli.run("config", path, "--config", config_path)

    assert_ok(update)
    assert_ok(read_back)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert _get_dotted(data, path) == expected
    assert _format_expected_value(expected) in _normalized_output(read_back.output)


@pytest.mark.release_validation
def test_context_registry_mutations_persist_and_remove_real_entries(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """Context add/default/use/clear/rename/remove should mutate the registry."""
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    gamma_root = tmp_path / "gamma"

    assert_ok(cli.run("init", "alpha", "--root", alpha_root, "--yes"))
    assert_ok(cli.run("init", "beta", "--root", beta_root, "--yes"))
    assert_ok(cli.run("init", "gamma-seed", "--root", gamma_root, "--yes"))
    assert_ok(cli.run("context", "remove", "gamma-seed", "--yes"))
    assert not gamma_root.exists()
    assert_ok(cli.run("context", "default", "alpha"))
    assert json.loads(cli.run("context", "ls", "--json").stdout)["default"] == "alpha"
    assert "alpha" in cli.run("context", "current").output

    assert_ok(cli.run("context", "use", "beta"))
    assert json.loads(cli.run("context", "ls", "--json").stdout)["default"] == "beta"
    assert_ok(cli.run("context", "clear"))
    assert json.loads(cli.run("context", "ls", "--json").stdout)["default"] is None

    gamma_root.mkdir()
    (gamma_root / "sftpwarden.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "project:",
                "  name: gamma",
                "provider:",
                "  type: yaml",
                "  path: users.yaml",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (gamma_root / "users.yaml").write_text("schema_version: 2\nusers: []\n", encoding="utf-8")
    assert gamma_root.exists()
    assert_ok(cli.run("context", "add", "gamma", "--root", gamma_root, "--yes"))
    registry = json.loads(cli.run("context", "ls", "--json").stdout)
    assert registry["contexts"]["gamma"]["root"] == str(gamma_root)

    assert_ok(cli.run("context", "rename", "gamma", "gamma-renamed"))
    registry = json.loads(cli.run("context", "ls", "--json").stdout)
    assert "gamma" not in registry["contexts"]
    assert registry["contexts"]["gamma-renamed"]["name"] == "gamma-renamed"

    assert_ok(cli.run("context", "remove", "gamma-renamed", "--yes"))
    registry = json.loads(cli.run("context", "ls", "--json").stdout)
    assert "gamma-renamed" not in registry["contexts"]


@pytest.mark.release_validation
@pytest.mark.parametrize("command_name", sorted(CONTEXT_FIELD_COMMANDS))
def test_context_dynamic_field_commands_persist_registry_changes(
    cli: ReleaseCli,
    tmp_path: Path,
    command_name: str,
) -> None:
    """Each dynamic context field command should persist the expected registry field."""
    field = CONTEXT_FIELD_COMMANDS[command_name]
    context_name = f"ctx-{command_name.replace('.', '-').replace('_', '-').replace('--', '-')}"
    root = tmp_path / "project"

    if field.startswith("remote.") or field == "storage":
        assert_ok(
            cli.run(
                "init",
                context_name,
                "--remote",
                "deploy@example.com:/srv/base",
                "--root",
                root,
                "--provider",
                "yaml",
                "--skip-checks",
                "--yes",
            )
        )
    else:
        assert_ok(cli.run("init", context_name, "--root", root, "--yes"))

    value, expected_context, expected_path, expected_value, extra_args = _context_field_mutation(
        context_name, field, tmp_path
    )
    result = cli.run(
        "context",
        command_name,
        value,
        "--context",
        context_name,
        *extra_args,
    )
    show = cli.run("context", "show", "--name", expected_context)

    assert_ok(result)
    assert_ok(show)
    data = json.loads(show.stdout)
    assert _get_dotted(data, expected_path) == expected_value


def _prepare_config_for_path(config_path: Path, path: str) -> None:
    """Make one config file valid before mutating a path with cross-field constraints."""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if path in {"provider.dsn", "provider.query", "provider.table"}:
        data["provider"]["type"] = "mysql"
        data["provider"]["dsn"] = "mysql://sftpwarden:sftpwarden@127.0.0.1:3306/sftpwarden"
    if path == "provider.collection":
        data["provider"]["type"] = "mongodb"
        data["provider"]["dsn"] = "mongodb://127.0.0.1:27017/sftpwarden"
    if path == "watcher.image":
        data["watcher"]["mode"] = "docker"
    if path == "auth.allow_password":
        data["auth"]["allow_public_key"] = True
    if path == "auth.allow_public_key":
        data["auth"]["allow_password"] = True
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _context_field_mutation(
    context_name: str,
    field: str,
    tmp_path: Path,
) -> tuple[str, str, str, Any, tuple[str | Path, ...]]:
    """Return value, final context, asserted field path, and extra CLI args."""
    context_values: dict[str, tuple[str, str, Any, tuple[str | Path, ...]]] = {
        "name": ("renamed-context", "name", "renamed-context", ()),
        "type": (
            "remote",
            "type",
            "remote",
            ("--remote", "deploy@example.com:/srv/type-context", "--yes"),
        ),
        "root": (
            str(tmp_path / "migrated-root"),
            "root",
            str(tmp_path / "migrated-root"),
            ("--yes",),
        ),
        "config": (
            str(tmp_path / "custom-sftpwarden.yaml"),
            "config",
            str(tmp_path / "custom-sftpwarden.yaml"),
            (),
        ),
        "provider": ("csv", "provider", "csv", ()),
        "critical": ("true", "critical", True, ()),
        "storage": ("remote-only", "storage", "remote-only", ("--yes",)),
        "watcher_required": ("true", "watcher_required", True, ()),
        "remote.remote_root": (
            "/srv/updated-root",
            "remote.remote_root",
            "/srv/updated-root",
            ("--yes",),
        ),
        "remote.remote_config": (
            "/srv/updated-root/custom.yaml",
            "remote.remote_config",
            "/srv/updated-root/custom.yaml",
            (),
        ),
        "remote.ssh_key": (
            "/home/deploy/.ssh/release-key",
            "remote.ssh_key",
            "/home/deploy/.ssh/release-key",
            (),
        ),
        "remote.host": (
            "release.example.com",
            "remote.host",
            "release.example.com",
            (),
        ),
        "remote.user": (
            "release",
            "remote.user",
            "release",
            (),
        ),
        "remote.port": (
            "2224",
            "remote.port",
            2224,
            (),
        ),
        "remote.compose_file": (
            "compose.release.yml",
            "remote.compose_file",
            "compose.release.yml",
            (),
        ),
    }
    value, expected_path, expected_value, extra_args = context_values[field]
    expected_context = expected_value if field == "name" else context_name
    return value, str(expected_context), expected_path, expected_value, extra_args


def _get_dotted(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        current = current[part]
    return current


def _format_expected_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return " ".join(str(value).split())


def _normalized_output(output: str) -> str:
    return " ".join(output.split())
