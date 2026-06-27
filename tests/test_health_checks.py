from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import sftpwarden.cli_commands.core as core_commands
import sftpwarden.cli_commands.runtime as runtime_commands
import sftpwarden.render.compose as compose_module
import sftpwarden.services.health as health_services
from sftpwarden.cli import app
from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import (
    ContextEntry,
    ContextRegistry,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.render.compose import compose_text
from sftpwarden.services.health import (
    HealthCheck,
    HealthReport,
    project_health,
    runtime_health,
)
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils.errors import ConfigError, RuntimeError


def test_project_health_report_and_compose_healthcheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project health reports pass status and Compose includes runtime healthcheck."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text("users: []\n", encoding="utf-8")
    compose = (
        yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
        if (root / "docker-compose.yml").exists()
        else None
    )
    save_registry(
        ContextRegistry(
            default="dev",
            contexts={"dev": local_context("dev", root, ProviderType.YAML)},
        )
    )
    monkeypatch.setattr(
        health_services,
        "runtime_health_from_context",
        lambda _entry: [HealthCheck("runtime", "pass", "ok")],
    )

    (root / "docker-compose.yml").write_text(compose_text(config, root), encoding="utf-8")
    report = project_health("dev")
    rendered = yaml.safe_load(compose_text(config, root))

    assert compose is None
    assert report.healthy
    assert rendered["services"]["sftpwarden"]["healthcheck"]["test"] == [
        "CMD",
        "sftpwarden",
        "runtime",
        "health",
        "--config",
        "/etc/sftpwarden/sftpwarden.yaml",
    ]


def test_compose_runtime_image_resolves_local_pip_and_custom_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose builds local source images and pulls packaged or custom images."""
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev")

    local = yaml.safe_load(compose_text(config, root))["services"]["sftpwarden"]

    assert local["image"] == "sftpwarden:local"
    assert local["build"]["dockerfile"] == "docker/runtime/Dockerfile"

    monkeypatch.setattr(compose_module, "LOCAL_RUNTIME_DOCKERFILE", tmp_path / "missing")
    monkeypatch.setattr(compose_module, "get_version", lambda: "9.9.9")

    packaged = yaml.safe_load(compose_text(config, root))["services"]["sftpwarden"]

    assert packaged["image"] == "ghcr.io/kithuto/sftpwarden:9.9.9"
    assert "build" not in packaged

    config.docker.image = "registry.example.com/sftpwarden:test"
    custom = yaml.safe_load(compose_text(config, root))["services"]["sftpwarden"]

    assert custom["image"] == "registry.example.com/sftpwarden:test"
    assert "build" not in custom


def test_project_health_edges_and_runtime_context_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
) -> None:
    """Project health handles missing config, provider, compose and runtime states."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    remote = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="/opt/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="archive", contexts={"archive": remote}))
    assert health_services.project_health("archive").checks[0].status == "fail"

    root = tmp_path / "bad-config"
    root.mkdir()
    (root / "sftpwarden.yaml").write_text("project: {}\n", encoding="utf-8")
    bad_entry = local_context("bad", root, ProviderType.YAML)
    save_registry(ContextRegistry(default="bad", contexts={"bad": bad_entry}))
    assert health_services.project_health("bad").checks[0].status == "fail"

    root, _entry = local_project_factory(root=tmp_path / "ok" / "project")
    (root / "users.yaml").unlink()
    save_registry(
        ContextRegistry(
            default="dev", contexts={"dev": local_context("dev", root, ProviderType.YAML)}
        )
    )
    report = health_services.project_health("dev")
    assert any(check.name == "provider" and check.status == "fail" for check in report.checks)
    assert any(check.name == "provider-file" and check.status == "fail" for check in report.checks)

    (root / "users.yaml").write_text("users: []\n", encoding="utf-8")
    original_runtime_health = health_services.runtime_health_from_context
    monkeypatch.setattr(
        health_services,
        "runtime_health_from_context",
        lambda _entry: [HealthCheck("runtime", "pass", "ok")],
    )
    (root / "docker-compose.yml").unlink()
    report = health_services.project_health("dev")
    assert any(
        check.name == "compose" and check.status == "warn" and "missing" in check.message
        for check in report.checks
    )

    (root / "docker-compose.yml").write_text("stale: true\n", encoding="utf-8")
    report = health_services.project_health("dev")
    assert report.as_dict()["healthy"]
    assert any(check.name == "compose" and check.status == "warn" for check in report.checks)
    monkeypatch.setattr(health_services, "runtime_health_from_context", original_runtime_health)

    monkeypatch.setattr(
        health_services,
        "run",
        lambda command, **_kwargs: CommandResult(command, 0, "ok", ""),
    )
    assert (
        health_services.runtime_health_from_context(local_context("dev", root, ProviderType.YAML))[
            0
        ].status
        == "pass"
    )
    remote_local_sync = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="/opt/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    assert health_services.runtime_health_from_context(remote_local_sync)[0].status == "pass"
    missing_remote = remote_local_sync.model_copy(update={"remote": None})
    assert health_services.runtime_health_from_context(missing_remote)[0].status == "fail"

    monkeypatch.setattr(
        health_services,
        "run",
        lambda command, **_kwargs: CommandResult(command, 1, "", "not running"),
    )
    assert (
        health_services.runtime_health_from_context(local_context("dev", root, ProviderType.YAML))[
            0
        ].status
        == "warn"
    )


def test_runtime_health_success_and_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime health validates config, runtime dirs, provider readability and sshd config."""
    invalid = runtime_health(tmp_path / "missing.yaml")
    assert not invalid.healthy

    root = tmp_path / "runtime"
    root.mkdir()
    config = default_project_config("runtime")
    config.provider.path = str(root / "users.yaml")
    config.server.state_dir = str(root / "state")
    config.server.data_dir = str(root / "data")
    config.server.host_keys_dir = str(root / "host_keys")
    for path in (config.server.state_dir, config.server.data_dir, config.server.host_keys_dir):
        Path(path).mkdir()
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text("users: []\n", encoding="utf-8")
    authorized = root / "authorized"
    authorized.mkdir()
    sshd_config = root / "sshd_config"
    sshd_config.write_text("Port 22\n", encoding="utf-8")
    monkeypatch.setattr(health_services, "RUNTIME_AUTHORIZED_KEYS_DIR", authorized)
    monkeypatch.setattr(health_services, "RUNTIME_SSHD_CONFIG", sshd_config)

    report = runtime_health(root / "sftpwarden.yaml")
    assert report.healthy

    Path(config.server.state_dir).rmdir()
    missing_dir = runtime_health(root / "sftpwarden.yaml")
    assert any(check.name == "state-dir" and check.status == "fail" for check in missing_dir.checks)

    (root / "users.yaml").unlink()
    failed = runtime_health(root / "sftpwarden.yaml")
    assert any(check.name == "provider" and check.status == "fail" for check in failed.checks)


def test_health_cli_output_and_error_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health CLI wrappers return proper JSON, exit codes and errors."""
    runner = CliRunner()
    healthy = HealthReport("dev", [HealthCheck("config", "pass", "ok")])
    failed = HealthReport("dev", [HealthCheck("config", "fail", "bad")])

    monkeypatch.setattr(core_commands, "project_health", lambda _context: healthy)
    assert runner.invoke(app, ["health"]).exit_code == 0
    result = runner.invoke(app, ["health", "--json"])
    assert json.loads(result.output)["healthy"]

    monkeypatch.setattr(core_commands, "project_health", lambda _context: failed)
    assert runner.invoke(app, ["health", "--json"]).exit_code == 1
    monkeypatch.setattr(
        core_commands,
        "project_health",
        lambda _context: (_ for _ in ()).throw(ConfigError("bad config")),
    )
    assert runner.invoke(app, ["health"]).exit_code == 1

    monkeypatch.setattr(runtime_commands, "runtime_health_report", lambda _config: healthy)
    assert runner.invoke(app, ["runtime", "health", "--config", "x"]).exit_code == 0
    runtime_json = runner.invoke(app, ["runtime", "health", "--config", "x", "--json"])
    assert json.loads(runtime_json.output)["healthy"]

    monkeypatch.setattr(runtime_commands, "runtime_health_report", lambda _config: failed)
    assert runner.invoke(app, ["runtime", "health", "--config", "x", "--json"]).exit_code == 1
    monkeypatch.setattr(
        runtime_commands,
        "runtime_health_report",
        lambda _config: (_ for _ in ()).throw(RuntimeError("runtime failed")),
    )
    assert runner.invoke(app, ["runtime", "health", "--config", "x"]).exit_code == 1
