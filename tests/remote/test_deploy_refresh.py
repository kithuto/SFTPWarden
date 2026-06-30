from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.refresh.core as refresh_module
import sftpwarden.remote.deploy as deploy_module
import sftpwarden.render.compose as compose_module
from sftpwarden.cli import app
from sftpwarden.config import (
    DeployTarget,
    ProviderType,
    default_project_config,
    write_config,
)
from sftpwarden.contexts import (
    ContextRegistry,
    ContextType,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.providers import empty_provider_text
from sftpwarden.refresh.core import refresh_context
from sftpwarden.remote.deploy import deploy_context
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.errors import RuntimeError as SFTPWardenRuntimeError


def test_deploy_remote_local_sync_dry_run_includes_sync_and_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "prod-project"
    root.mkdir()
    config = default_project_config("prod")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    runner = CliRunner()

    result = runner.invoke(app, ["deploy", "--context", "prod", "--dry-run"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "mkdir -p /opt/sftpwarden" in output
    assert "rsync" in output
    assert "docker compose -f docker-compose.yml pull" in output
    assert "docker compose -f docker-compose.yml up -d" in output
    assert "--build" not in output


def test_deploy_remote_only_dry_run_validates_remote_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    entry = remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="archive", contexts={"archive": entry}))
    runner = CliRunner()

    result = runner.invoke(app, ["deploy", "--context", "archive", "--dry-run"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "test -f /opt/sftpwarden/sftpwarden.yaml" in output
    assert "rsync" not in output
    assert "docker compose -f docker-compose.yml pull" in output
    assert "docker compose -f docker-compose.yml up -d" in output
    assert "--build" not in output


def test_deploy_context_accepts_injected_runner(tmp_path: Path) -> None:
    root = tmp_path / "dev-project"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = local_context("dev", root, config.provider.type)
    calls: list[tuple[list[str], str | None]] = []

    def fake_runner(command: list[str], *, cwd: str | None = None) -> None:
        calls.append((command, cwd))

    result = deploy_context(entry, runner=fake_runner)

    assert result == "Deployed dev."
    assert calls[0][0] == ["docker", "compose", "version"]
    assert calls[1][0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "up",
        "-d",
        "--build",
    ]
    assert calls[2][0] == ["docker", "compose", "-f", "docker-compose.yml", "ps", "sftpwarden"]
    assert {cwd for _, cwd in calls} == {str(root)}


def test_deploy_context_pulls_packaged_runtime_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Packaged installs pull the GHCR runtime image instead of building locally."""
    monkeypatch.setattr(compose_module, "LOCAL_RUNTIME_DOCKERFILE", tmp_path / "missing")
    monkeypatch.setattr(compose_module, "get_version", lambda: "9.9.9")
    root = tmp_path / "dev-project"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = local_context("dev", root, config.provider.type)
    calls: list[tuple[list[str], str | None]] = []

    def fake_runner(command: list[str], *, cwd: str | None = None) -> None:
        calls.append((command, cwd))

    result = deploy_context(entry, runner=fake_runner)
    compose_text = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert result == "Deployed dev."
    assert "ghcr.io/kithuto/sftpwarden:9.9.9" in compose_text
    assert "--build" not in " ".join(" ".join(command) for command, _cwd in calls)
    assert calls[1][0] == ["docker", "compose", "-f", "docker-compose.yml", "pull"]
    assert calls[2][0] == ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"]


def test_deploy_context_reports_missing_local_docker_compose(tmp_path: Path) -> None:
    root = tmp_path / "dev-project"
    root.mkdir()
    config = default_project_config("dev")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = local_context("dev", root, config.provider.type)

    def fake_runner(command: list[str], *, cwd: str | None = None) -> None:
        if command == ["docker", "compose", "version"]:
            raise SFTPWardenRuntimeError(
                "Deploy command failed.",
                suggestion="docker: compose is not a docker command",
            )

    with pytest.raises(SFTPWardenRuntimeError, match="Docker Compose is not available"):
        deploy_context(entry, runner=fake_runner)


def test_deploy_plan_and_required_files_error_edges(tmp_path: Path) -> None:
    malformed_remote = local_context("broken", tmp_path / "broken", ProviderType.YAML)
    malformed_remote.type = ContextType.REMOTE
    malformed_remote.remote = None

    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_module.deploy_plan(malformed_remote)
    with pytest.raises(ContextError, match="missing local settings"):
        deploy_module._local_deploy_plan(
            local_context("local", tmp_path / "missing", ProviderType.YAML).model_copy(
                update={"root": "", "config": ""}
            )
        )
    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_module._remote_local_sync_deploy_plan(malformed_remote)
    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_module._remote_only_deploy_plan(malformed_remote)
    remote_missing_local = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    remote_missing_local.root = ""
    remote_missing_local.config = ""
    with pytest.raises(ContextError, match="missing local settings"):
        deploy_module._remote_local_sync_deploy_plan(remote_missing_local)

    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("dev")
    config_path = root / "sftpwarden.yaml"
    compose_path = root / "docker-compose.yml"
    write_config(config_path, config)
    compose_path.write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(SFTPWardenRuntimeError, match="Required deploy file"):
        deploy_module.required_sync_files(root, config_path=config_path, compose_path=compose_path)

    users_path = root / "users.yaml"
    users_path.write_text(empty_provider_text(ProviderType.YAML), encoding="utf-8")
    excluded = root / ".env"
    excluded.write_text("SECRET=1\n", encoding="utf-8")
    with pytest.raises(SFTPWardenRuntimeError, match="Refusing to sync"):
        deploy_module.required_sync_files(root, config_path=config_path, compose_path=excluded)


def test_deploy_remote_runs_verification_and_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config = default_project_config("prod")
    write_config(root / "sftpwarden.yaml", config)
    (root / "users.yaml").write_text(empty_provider_text(config.provider.type), encoding="utf-8")
    entry = remote_context(
        name="prod",
        provider=config.provider.type,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    verified: list[str] = []
    commands: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        deploy_module,
        "verify_remote_runtime_requirements",
        lambda remote: verified.append(remote.host),
    )

    message = deploy_context(
        entry,
        runner=lambda command, *, cwd=None: commands.append((command, cwd)),
    )

    assert message == "Deployed prod."
    assert verified == ["example.com"]
    assert commands
    assert {cwd for _command, cwd in commands} == {None}


def test_deploy_context_rechecks_remote_settings_after_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed_remote = local_context("broken", tmp_path / "broken", ProviderType.YAML)
    malformed_remote.type = ContextType.REMOTE
    malformed_remote.remote = None
    monkeypatch.setattr(
        deploy_module,
        "deploy_plan",
        lambda _context: deploy_module.DeployPlan(commands=[]),
    )

    with pytest.raises(ContextError, match="missing remote settings"):
        deploy_context(malformed_remote)


def test_run_command_delegates_to_checked_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        deploy_module,
        "run_checked",
        lambda command, **kwargs: calls.append((command, kwargs.get("cwd"))),
    )

    cwd = str(tmp_path / "project")
    deploy_module.run_command(["docker", "compose", "ps"], cwd=cwd)

    assert calls == [(["docker", "compose", "ps"], cwd)]


def test_refresh_context_executes_local_and_remote_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = local_context("dev", tmp_path / "dev", ProviderType.YAML)
    remote = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=tmp_path,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    calls: list[tuple[list[str], str | None]] = []

    class Result:
        stdout = ""

    def fake_run_checked(command: list[str], **kwargs: object) -> Result:
        calls.append((command, kwargs.get("cwd")))  # type: ignore[arg-type]
        return Result()

    monkeypatch.setattr(refresh_module, "run_checked", fake_run_checked)

    assert refresh_context(local) == "Refreshed dev."
    assert refresh_context(remote) == "Refreshed prod."
    assert calls[0][1] == str(tmp_path / "dev")
    assert calls[1][0][0] == "ssh"


def test_refresh_context_executes_kubernetes_runtime_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "kube"
    project.mkdir()
    config = default_project_config("prod", ProviderType.POSTGRESQL, dsn="postgresql://db/sftp")
    config.deploy.target = DeployTarget.KUBERNETES
    config.kubernetes.namespace = "sftp"
    config.kubernetes.release = "prod"
    write_config(project / "sftpwarden.yaml", config)
    entry = local_context("prod", project, ProviderType.POSTGRESQL)
    calls: list[list[str]] = []

    class Result:
        stdout = ""

    def fake_run_checked(command: list[str], **_kwargs: object) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr(refresh_module, "run_checked", fake_run_checked)

    assert refresh_context(entry, dry_run=True).startswith("(dry-run) kubectl -n sftp exec prod-0")
    assert refresh_context(entry) == "Refreshed prod."
    assert calls == [
        [
            "kubectl",
            "-n",
            "sftp",
            "exec",
            "prod-0",
            "-c",
            "sftpwarden",
            "--",
            "sftpwarden",
            "runtime",
            "refresh",
            "--config",
            "/etc/sftpwarden/sftpwarden.yaml",
        ]
    ]


def test_refresh_context_rejects_remote_without_settings(tmp_path: Path) -> None:
    malformed_remote = local_context("broken", tmp_path / "broken", ProviderType.YAML)
    malformed_remote.type = ContextType.REMOTE
    malformed_remote.remote = None

    with pytest.raises(ContextError, match="missing remote settings"):
        refresh_context(malformed_remote)
