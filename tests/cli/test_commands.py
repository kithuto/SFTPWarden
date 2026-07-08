from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import sftpwarden.cli_commands.context as context_commands
import sftpwarden.cli_commands.core as core_commands
import sftpwarden.cli_commands.helm as helm_commands
import sftpwarden.cli_commands.init as init_commands
import sftpwarden.cli_commands.kubernetes as kube_commands
import sftpwarden.cli_commands.provider as provider_commands
import sftpwarden.cli_commands.runtime as runtime_commands
import sftpwarden.cli_commands.users as user_commands
import sftpwarden.services.cli_workflows as cli_workflows
import sftpwarden.utils.files as file_utils
from sftpwarden.cli import app
from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    ProviderType,
    default_project_config,
    load_config,
    write_config,
)
from sftpwarden.contexts import (
    ContextRegistry,
    load_registry,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.runtime import ResolvedUser, RuntimeAction, RuntimePlan, RuntimeState
from sftpwarden.system.commands import CommandResult
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.watcher import WatchTarget

TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"
SECOND_TEST_KEY = "ssh-ed25519 ZmFrZS1rZXktMg=="
THIRD_TEST_KEY = "ssh-ed25519 ZmFrZS1rZXktMw=="
FOURTH_TEST_KEY = "ssh-ed25519 ZmFrZS1rZXktNA=="


def test_version_command_matches_version_flag() -> None:
    """The friendly version command should match the historical flag."""
    runner = CliRunner()

    flag_result = runner.invoke(app, ["--version"])
    command_result = runner.invoke(app, ["version"])

    assert flag_result.exit_code == 0, flag_result.output
    assert command_result.exit_code == 0, command_result.output
    assert command_result.output == flag_result.output
    assert "SFTPWarden" in command_result.output


def test_init_named_context_creates_project_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    chmods: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        file_utils.os,
        "chmod",
        lambda path, mode: chmods.append((Path(path), mode)),
    )

    result = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    assert result.exit_code == 0, result.output
    config = load_config(root / "sftpwarden.yaml")
    assert config.project.name == "dev"
    assert (root / "users.yaml").exists()
    assert (root / "docker-compose.yml").exists()
    assert (root / "sftpwarden.yaml", 0o600) in chmods
    assert (root / "users.yaml", 0o600) in chmods


def test_init_user_schema_option_and_command_first_key_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    v1_root = tmp_path / "v1-project"
    v2_root = tmp_path / "v2-project"
    runner = CliRunner()

    v1_result = runner.invoke(
        app,
        ["init", "simple", "--root", str(v1_root), "--user-schema", "1", "--yes"],
    )
    v2_result = runner.invoke(app, ["init", "dev", "--root", str(v2_root), "--yes"])
    create_result = runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    key_add_result = runner.invoke(
        app,
        [
            "user",
            "key",
            "add",
            "alice",
            "prod-ci",
            "--public-key",
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    key_list_result = runner.invoke(
        app,
        ["user", "key", "list", "alice", "--context", "dev"],
    )

    assert v1_result.exit_code == 0, v1_result.output
    assert v2_result.exit_code == 0, v2_result.output
    assert load_config(v1_root / "sftpwarden.yaml").provider.user_schema == 1
    assert load_config(v2_root / "sftpwarden.yaml").provider.user_schema == 2
    assert "schema_version: 2" in (v2_root / "users.yaml").read_text(encoding="utf-8")
    assert create_result.exit_code == 0, create_result.output
    assert key_add_result.exit_code == 0, key_add_result.output
    assert key_list_result.exit_code == 0, key_list_result.output
    assert "prod-ci" in key_list_result.output


def test_user_and_named_key_cli_lifecycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    imported_dir = tmp_path / "keys"
    imported_dir.mkdir()
    (imported_dir / "laptop.pub").write_text(FOURTH_TEST_KEY, encoding="utf-8")
    refreshes: list[str] = []
    monkeypatch.setattr(
        user_commands,
        "print_refresh_after_user_change",
        lambda context: refreshes.append(context.name),
    )
    runner = CliRunner()

    init = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    create = runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
        ],
    )
    disable_user = runner.invoke(app, ["user", "disable", "alice", "--context", "dev"])
    enable_user = runner.invoke(app, ["user", "enable", "alice", "--context", "dev"])
    add = runner.invoke(
        app,
        [
            "user",
            "key",
            "add",
            "alice",
            "prod-ci",
            "--public-key",
            TEST_KEY,
            "--context",
            "dev",
        ],
    )
    show = runner.invoke(app, ["user", "key", "show", "alice", "prod-ci", "--context", "dev"])
    disable_key = runner.invoke(
        app, ["user", "key", "disable", "alice", "prod-ci", "--context", "dev"]
    )
    enable_key = runner.invoke(
        app, ["user", "key", "enable", "alice", "prod-ci", "--context", "dev"]
    )
    rename = runner.invoke(
        app,
        ["user", "key", "rename", "alice", "prod-ci", "prod-renamed", "--context", "dev"],
    )
    rotate = runner.invoke(
        app,
        [
            "user",
            "key",
            "rotate",
            "alice",
            "prod-renamed",
            "--public-key",
            SECOND_TEST_KEY,
            "--context",
            "dev",
        ],
    )
    expire = runner.invoke(
        app,
        [
            "user",
            "key",
            "expire",
            "alice",
            "prod-renamed",
            "--at",
            "2027-01-01",
            "--context",
            "dev",
        ],
    )
    imported = runner.invoke(
        app,
        [
            "user",
            "key",
            "import",
            "alice",
            "--from-dir",
            str(imported_dir),
            "--context",
            "dev",
        ],
    )
    removed = runner.invoke(
        app,
        ["user", "key", "remove", "alice", "laptop", "--yes", "--context", "dev"],
    )

    results = [
        init,
        create,
        disable_user,
        enable_user,
        add,
        show,
        disable_key,
        enable_key,
        rename,
        rotate,
        expire,
        imported,
        removed,
    ]
    assert all(result.exit_code == 0 for result in results), "\n".join(
        result.output for result in results
    )
    assert '"name": "prod-ci"' in show.output
    assert "prod-renamed" in (root / "users.yaml").read_text(encoding="utf-8")
    assert "laptop" not in (root / "users.yaml").read_text(encoding="utf-8")
    assert len(refreshes) == 11


def test_user_and_key_cli_error_paths_are_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    empty_import_dir = tmp_path / "empty-keys"
    empty_import_dir.mkdir()
    runner = CliRunner()

    init = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    create = runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    add = runner.invoke(
        app,
        [
            "user",
            "key",
            "add",
            "alice",
            "prod-ci",
            "--public-key",
            TEST_KEY,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    failures = [
        runner.invoke(app, ["user", "disable", "missing", "--context", "dev"]),
        runner.invoke(app, ["user", "enable", "missing", "--context", "dev"]),
        runner.invoke(app, ["user", "key", "list", "missing", "--context", "dev"]),
        runner.invoke(app, ["user", "key", "show", "alice", "missing", "--context", "dev"]),
        runner.invoke(
            app,
            [
                "user",
                "key",
                "add",
                "missing",
                "new-key",
                "--public-key",
                SECOND_TEST_KEY,
                "--context",
                "dev",
            ],
        ),
        runner.invoke(
            app,
            ["user", "key", "remove", "alice", "missing", "--yes", "--context", "dev"],
        ),
        runner.invoke(
            app,
            [
                "user",
                "key",
                "import",
                "alice",
                "--from-dir",
                str(empty_import_dir),
                "--context",
                "dev",
            ],
        ),
        runner.invoke(app, ["user", "key", "disable", "alice", "missing", "--context", "dev"]),
    ]
    cancelled_remove = runner.invoke(
        app,
        ["user", "key", "remove", "alice", "prod-ci", "--context", "dev"],
        input="n\n",
    )

    assert init.exit_code == 0, init.output
    assert create.exit_code == 0, create.output
    assert add.exit_code == 0, add.output
    assert all(result.exit_code != 0 for result in failures)
    assert cancelled_remove.exit_code == 1


def test_init_without_root_uses_current_directory_and_sets_active_context(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    root.mkdir()
    monkeypatch.chdir(root)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "dev", "--yes"])
    current = runner.invoke(app, ["context", "current"])

    assert result.exit_code == 0, result.output
    assert (root / "sftpwarden.yaml").exists()
    assert (root / "users.yaml").exists()
    assert (root / "docker-compose.yml").exists()
    assert load_config(root / "sftpwarden.yaml").project.name == "dev"
    assert load_registry().default == "dev"
    assert current.exit_code == 0, current.output
    assert current.output.strip() == "dev"


def test_context_commands_require_init_before_first_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    commands = [
        ["info"],
        ["context", "ls"],
        ["sync", "--dry-run"],
        ["watcher", "status"],
    ]

    for command in commands:
        result = runner.invoke(app, command)

        assert result.exit_code == 1, result.output
        assert "No SFTPWarden context has been initialized." in result.output
        assert "Run `sftpwarden init <name>` first." in result.output


def test_deploy_uses_active_local_context_and_builds_compose(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    root.mkdir()
    monkeypatch.chdir(root)
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--yes"])

    result = runner.invoke(app, ["deploy", "--dry-run"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "docker compose -f docker-compose.yml up -d --build" in output


def test_plan_explains_detected_changes_will_be_applied(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    result = runner.invoke(app, ["plan"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "User/provider changes detected" in output
    assert "These actions will be applied by `sftpwarden refresh`" in output


def test_plan_explains_detected_config_changes_require_deploy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(app, ["config", "server.port", "2200"])

    result = runner.invoke(app, ["plan"])
    output = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert "Configuration/deploy changes detected" in output
    assert "These changes will be applied by `sftpwarden deploy`" in output
    assert "`sftpwarden refresh` only applies user/provider changes" in output
    assert "docker-compose.yml differs from current configuration" in output


def test_plan_json_reports_detected_config_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(app, ["config", "server.port", "2200"])

    result = runner.invoke(app, ["plan", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["deploy_config_changed"]
    assert data["deploy_config_reasons"] == [
        "docker-compose.yml differs from current configuration"
    ]


def test_config_command_reads_and_updates_project_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    read_result = runner.invoke(app, ["config", "server.port"])
    null_result = runner.invoke(app, ["config", "provider.dsn"])
    update_result = runner.invoke(app, ["config", "server.port", "2200"])
    health_update = runner.invoke(app, ["config", "healthcheck.interval_seconds", "45"])
    probe_update = runner.invoke(app, ["config", "kubernetes.liveness_probe.period_seconds", "45"])

    config = load_config(root / "sftpwarden.yaml")
    assert read_result.exit_code == 0, read_result.output
    assert read_result.output.strip() == "2222"
    assert null_result.exit_code == 0, null_result.output
    assert null_result.output.strip() == "null"
    assert update_result.exit_code == 0, update_result.output
    assert health_update.exit_code == 0, health_update.output
    assert probe_update.exit_code == 0, probe_update.output
    assert config.server.port == 2200
    assert config.healthcheck.interval_seconds == 45
    assert config.kubernetes.liveness_probe.period_seconds == 45


def test_config_string_fields_keep_numeric_cli_values_as_strings(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    root_update = runner.invoke(app, ["config", "isolation.root_permissions", "755"])
    upload_update = runner.invoke(app, ["config", "isolation.upload_permissions", "750"])

    config = load_config(root / "sftpwarden.yaml")
    assert root_update.exit_code == 0, root_update.output
    assert upload_update.exit_code == 0, upload_update.output
    assert config.isolation.root_permissions == "755"
    assert config.isolation.upload_permissions == "750"


def test_config_project_name_renames_active_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(app, ["config", "project.name", "dev2"])
    current = runner.invoke(app, ["context", "current"])
    registry = load_registry()

    assert result.exit_code == 0, result.output
    assert current.exit_code == 0, current.output
    assert current.output.strip() == "dev2"
    assert registry.default == "dev2"
    assert "dev" not in registry.contexts
    assert load_config(root / "sftpwarden.yaml").project.name == "dev2"


def test_context_name_updates_registry_and_project_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(app, ["context", "name", "dev2"])
    registry = load_registry()

    assert result.exit_code == 0, result.output
    assert registry.default == "dev2"
    assert "dev2" in registry.contexts
    assert "dev" not in registry.contexts
    assert load_config(root / "sftpwarden.yaml").project.name == "dev2"


def test_context_root_migrates_project_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    old_root = tmp_path / "old-project"
    new_root = tmp_path / "new-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(old_root), "--yes"])

    result = runner.invoke(
        app,
        ["context", "root", str(new_root), "--yes", "--delete-old-root"],
    )
    registry = load_registry()
    entry = registry.contexts["dev"]

    assert result.exit_code == 0, result.output
    assert not old_root.exists()
    assert (new_root / "sftpwarden.yaml").exists()
    assert (new_root / "users.yaml").exists()
    assert entry.root == str(new_root)
    assert entry.config == str(new_root / "sftpwarden.yaml")


def test_context_type_converts_between_local_and_remote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    remote_result = runner.invoke(
        app,
        [
            "context",
            "type",
            "remote",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--yes",
        ],
    )
    local_result = runner.invoke(app, ["context", "type", "local", "--yes"])
    entry = load_registry().contexts["dev"]

    assert remote_result.exit_code == 0, remote_result.output
    assert local_result.exit_code == 0, local_result.output
    assert entry.type == "local"
    assert entry.remote is None
    assert entry.root == str(root)


def test_context_type_warns_when_installed_watcher_has_no_local_sync_targets(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "init",
            "prod",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            str(root),
            "--critical",
            "--skip-checks",
            "--yes",
            "--watcher",
            "systemd",
        ],
    )

    result = runner.invoke(app, ["context", "type", "local", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Watcher uninstalled" in result.output


def test_manual_project_name_change_reconciles_registered_context(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    config = load_config(root / "sftpwarden.yaml")
    config.project.name = "manual"
    write_config(root / "sftpwarden.yaml", config)

    current_before_deploy = runner.invoke(app, ["context", "current"])
    deploy = runner.invoke(app, ["deploy", "--dry-run"])
    current_after_deploy = runner.invoke(app, ["context", "current"])
    registry = load_registry()

    assert current_before_deploy.exit_code == 0, current_before_deploy.output
    assert current_before_deploy.output.strip() == "dev"
    assert deploy.exit_code == 0, deploy.output
    assert current_after_deploy.exit_code == 0, current_after_deploy.output
    assert current_after_deploy.output.strip() == "manual"
    assert registry.default == "manual"
    assert "manual" in registry.contexts
    assert "dev" not in registry.contexts


def test_init_sql_provider_can_create_missing_table(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "sql-project"
    created: list[bool] = []
    seen_user_schemas: list[int] = []

    class FakeSQLProvider:
        def table_exists(self) -> bool:
            return False

        def create_table(self) -> None:
            created.append(True)

    def fake_provider_from_config(_project_root: Path, config) -> FakeSQLProvider:
        seen_user_schemas.append(config.provider.user_schema)
        return FakeSQLProvider()

    monkeypatch.setattr(init_commands, "provider_from_config", fake_provider_from_config)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "sql-dev",
            "--root",
            str(root),
            "--provider",
            "mysql",
            "--dsn",
            "mysql://user:pass@localhost/sftp",
            "--create-table",
            "--yes",
        ],
    )

    config = load_config(root / "sftpwarden.yaml")

    assert result.exit_code == 0, result.output
    assert created == [True]
    assert seen_user_schemas == [2]
    assert config.provider.type == "mysql"
    assert config.provider.dsn == "mysql://user:pass@localhost/sftp"
    assert not (root / "users.yaml").exists()


def test_init_sql_provider_can_abort_when_table_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "sql-project"

    class FakeSQLProvider:
        def table_exists(self) -> bool:
            return False

        def create_table(self) -> None:
            raise AssertionError("table should not be created")

    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: FakeSQLProvider())
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "init",
            "sql-dev",
            "--root",
            str(root),
            "--provider",
            "postgresql",
            "--dsn",
            "postgresql://user:pass@localhost/sftp",
            "--no-create-table",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "SQL users table does not exist" in result.output
    assert not (root / "sftpwarden.yaml").exists()


def test_provider_schema_migration_writes_v2_and_updates_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "sql-project"
    root.mkdir()
    config = default_project_config(
        "dev",
        ProviderType.MYSQL,
        dsn="mysql://user:pass@localhost/sftp",
        user_schema=1,
    )
    write_config(root / "sftpwarden.yaml", config)
    entry = local_context("dev", root, ProviderType.MYSQL)
    written: list[ProviderUsers] = []

    class FakeProvider:
        def read(self) -> ProviderUsers:
            return ProviderUsers(
                schema_version=1,
                users=[
                    SFTPUser(
                        username="alice",
                        public_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"],
                    )
                ],
            )

        def write(self, users: ProviderUsers) -> None:
            written.append(users)

    monkeypatch.setattr(
        provider_commands,
        "resolve_provider_context",
        lambda **_kwargs: (entry, config, FakeProvider()),
    )

    result = provider_commands.migrate_provider_schema(
        to_schema=2,
        context=None,
        config=None,
        backup=False,
        yes=True,
        dry_run=False,
    )

    assert result["changed"]
    assert written[0].schema_version == 2
    assert written[0].users[0].keys[0].name.startswith("legacy-")
    assert load_config(root / "sftpwarden.yaml").provider.user_schema == 2


def test_provider_schema_migration_can_be_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "sql-project"
    root.mkdir()
    config = default_project_config(
        "dev",
        ProviderType.MYSQL,
        dsn="mysql://user:pass@localhost/sftp",
        user_schema=1,
    )
    entry = local_context("dev", root, ProviderType.MYSQL)
    wrote: list[ProviderUsers] = []

    class FakeProvider:
        def read(self) -> ProviderUsers:
            return ProviderUsers(
                schema_version=1,
                users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
            )

        def write(self, users: ProviderUsers) -> None:
            wrote.append(users)

    monkeypatch.setattr(
        provider_commands,
        "resolve_provider_context",
        lambda **_kwargs: (entry, config, FakeProvider()),
    )
    monkeypatch.setattr(provider_commands.typer, "confirm", lambda *_args, **_kwargs: False)

    with pytest.raises(provider_commands.typer.Exit):
        provider_commands.migrate_provider_schema(
            to_schema=2,
            context=None,
            config=None,
            backup=False,
            yes=False,
            dry_run=False,
        )

    assert wrote == []


def test_provider_schema_cli_show_migrate_keys_alias_and_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "v1-project"
    runner = CliRunner()

    init = runner.invoke(
        app,
        ["init", "simple", "--root", str(root), "--user-schema", "1", "--yes"],
    )
    create = runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--public-key",
            TEST_KEY,
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "simple",
            "--no-refresh",
        ],
    )
    show = runner.invoke(app, ["provider", "schema", "show", "--context", "simple", "--json"])
    dry_run = runner.invoke(
        app,
        [
            "provider",
            "schema",
            "migrate",
            "--to",
            "2",
            "--context",
            "simple",
            "--dry-run",
            "--json",
        ],
    )
    migrated = runner.invoke(
        app,
        ["provider", "schema", "migrate", "--to", "2", "--context", "simple", "--yes"],
    )
    already = runner.invoke(
        app,
        ["provider", "keys", "migrate", "--context", "simple", "--yes"],
    )
    show_text = runner.invoke(app, ["provider", "schema", "show", "--context", "simple"])
    bad_show = runner.invoke(app, ["provider", "schema", "show", "--context", "missing"])
    bad_migrate = runner.invoke(
        app,
        ["provider", "schema", "migrate", "--to", "99", "--context", "simple"],
    )

    assert init.exit_code == 0, init.output
    assert create.exit_code == 0, create.output
    assert json.loads(show.output)["provider_user_schema"] == 1
    assert json.loads(dry_run.output)["dry_run"]
    assert migrated.exit_code == 0, migrated.output
    assert "Migrated provider schema v1 -> v2" in migrated.output
    assert "Backup:" in migrated.output
    assert already.exit_code == 0, already.output
    assert "already uses schema v2" in already.output
    assert "Provider schema v2" in show_text.output
    assert bad_show.exit_code != 0
    assert bad_migrate.exit_code != 0
    assert load_config(root / "sftpwarden.yaml").provider.user_schema == 2


def test_config_user_schema_change_requires_confirmation_and_defers_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "v1-project"
    runner = CliRunner()

    init = runner.invoke(
        app,
        ["init", "simple", "--root", str(root), "--user-schema", "1", "--yes"],
    )
    create = runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--public-key",
            TEST_KEY,
            "--context",
            "simple",
            "--no-refresh",
        ],
    )
    cancelled = runner.invoke(
        app,
        ["config", "provider.user_schema", "2", "--context", "simple"],
        input="n\n",
    )
    accepted = runner.invoke(
        app,
        ["config", "provider.user_schema", "2", "--context", "simple"],
        input="y\n",
    )

    assert init.exit_code == 0, init.output
    assert create.exit_code == 0, create.output
    assert cancelled.exit_code == 1
    assert "requires" in cancelled.output or "require" in cancelled.output
    assert accepted.exit_code == 0, accepted.output
    assert load_config(root / "sftpwarden.yaml").provider.user_schema == 2
    assert "schema_version: 2" not in (root / "users.yaml").read_text(encoding="utf-8")


def test_config_user_schema_change_without_migration_does_not_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "v2-project"
    runner = CliRunner()

    init = runner.invoke(app, ["init", "advanced", "--root", str(root), "--yes"])
    update = runner.invoke(app, ["config", "provider.user_schema", "2", "--context", "advanced"])

    assert init.exit_code == 0, init.output
    assert update.exit_code == 0, update.output
    assert "require a provider data migration" not in update.output


def test_deploy_applies_manual_user_schema_change_before_deployment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "v1-project"
    runner = CliRunner()
    monkeypatch.setattr(
        core_commands,
        "apply_deployment_plan",
        lambda *_args, **_kwargs: "Deployed simple.",
    )

    init = runner.invoke(
        app,
        ["init", "simple", "--root", str(root), "--user-schema", "1", "--yes"],
    )
    create = runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--public-key",
            TEST_KEY,
            "--context",
            "simple",
            "--no-refresh",
        ],
    )
    config = load_config(root / "sftpwarden.yaml")
    write_config(
        root / "sftpwarden.yaml",
        config.model_copy(
            update={"provider": config.provider.model_copy(update={"user_schema": 2})}
        ),
    )

    deployed = runner.invoke(app, ["deploy", "--context", "simple", "--yes"])

    assert init.exit_code == 0, init.output
    assert create.exit_code == 0, create.output
    assert deployed.exit_code == 0, deployed.output
    assert "Migrated provider schema v1 -> v2" in deployed.output
    assert "schema_version: 2" in (root / "users.yaml").read_text(encoding="utf-8")


def test_deploy_can_cancel_pending_user_schema_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "v1-project"
    runner = CliRunner()
    monkeypatch.setattr(
        core_commands,
        "apply_deployment_plan",
        lambda *_args, **_kwargs: pytest.fail("deploy should not run"),
    )

    runner.invoke(app, ["init", "simple", "--root", str(root), "--user-schema", "1", "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--public-key",
            TEST_KEY,
            "--context",
            "simple",
            "--no-refresh",
        ],
    )
    config = load_config(root / "sftpwarden.yaml")
    write_config(
        root / "sftpwarden.yaml",
        config.model_copy(
            update={"provider": config.provider.model_copy(update={"user_schema": 2})}
        ),
    )

    dry_run = runner.invoke(app, ["deploy", "--context", "simple", "--dry-run"])
    show_schema = runner.invoke(app, ["provider", "schema", "show", "--context", "simple"])
    cancelled = runner.invoke(app, ["deploy", "--context", "simple"], input="n\n")

    assert dry_run.exit_code == 0, dry_run.output
    assert "Would migrate provider schema v1 -> v2" in dry_run.output
    assert show_schema.exit_code == 0, show_schema.output
    assert "Run `sftpwarden deploy` to migrate provider data" in show_schema.output
    assert cancelled.exit_code == 1
    assert "schema_version: 2" not in (root / "users.yaml").read_text(encoding="utf-8")


def test_deploy_equivalent_dry_runs_report_pending_user_schema_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "v1-project"
    runner = CliRunner()

    runner.invoke(app, ["init", "simple", "--root", str(root), "--user-schema", "1", "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "create",
            "alice",
            "--public-key",
            TEST_KEY,
            "--context",
            "simple",
            "--no-refresh",
        ],
    )
    config = load_config(root / "sftpwarden.yaml")
    kube_config = config.model_copy(
        update={
            "deploy": config.deploy.model_copy(update={"target": DeployTarget.KUBERNETES}),
            "kubernetes": config.kubernetes.model_copy(update={"mode": KubernetesMode.MANIFESTS}),
            "provider": config.provider.model_copy(update={"user_schema": 2}),
        }
    )
    write_config(root / "sftpwarden.yaml", kube_config)

    kube_text = runner.invoke(
        app,
        ["kube", "apply", "--context", "simple", "--dry-run"],
    )
    kube_dry_run = runner.invoke(
        app,
        ["kube", "apply", "--context", "simple", "--dry-run", "--json"],
    )
    helm_config = kube_config.model_copy(
        update={
            "kubernetes": kube_config.kubernetes.model_copy(update={"mode": KubernetesMode.HELM})
        }
    )
    write_config(root / "sftpwarden.yaml", helm_config)
    helm_text = runner.invoke(
        app,
        ["helm", "upgrade", "--context", "simple", "--dry-run"],
    )
    helm_dry_run = runner.invoke(
        app,
        ["helm", "upgrade", "--context", "simple", "--dry-run", "--json"],
    )

    assert kube_text.exit_code == 0, kube_text.output
    assert "Would migrate provider schema v1 -> v2" in kube_text.output
    assert kube_dry_run.exit_code == 0, kube_dry_run.output
    assert json.loads(kube_dry_run.output)["provider_schema"]["changed"]
    assert helm_text.exit_code == 0, helm_text.output
    assert "Would migrate provider schema v1 -> v2" in helm_text.output
    assert helm_dry_run.exit_code == 0, helm_dry_run.output
    assert json.loads(helm_dry_run.output)["provider_schema"]["changed"]
    assert "schema_version: 2" not in (root / "users.yaml").read_text(encoding="utf-8")


def test_deploy_equivalents_apply_pending_user_schema_migration_with_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    run_calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> CommandResult:
        run_calls.append(command)
        return CommandResult(command, 0, "ok\n", "")

    monkeypatch.setattr(kube_commands, "run", fake_run)
    monkeypatch.setattr(helm_commands, "run", fake_run)

    kube_root = tmp_path / "kube-project"
    runner.invoke(
        app, ["init", "kube-schema", "--root", str(kube_root), "--user-schema", "1", "--yes"]
    )
    kube_config = load_config(kube_root / "sftpwarden.yaml")
    write_config(
        kube_root / "sftpwarden.yaml",
        kube_config.model_copy(
            update={
                "deploy": kube_config.deploy.model_copy(update={"target": DeployTarget.KUBERNETES}),
                "kubernetes": kube_config.kubernetes.model_copy(
                    update={"mode": KubernetesMode.MANIFESTS}
                ),
                "provider": kube_config.provider.model_copy(update={"user_schema": 2}),
            }
        ),
    )

    kube_apply = runner.invoke(app, ["kube", "apply", "--context", "kube-schema", "--yes"])

    helm_root = tmp_path / "helm-project"
    runner.invoke(
        app, ["init", "helm-schema", "--root", str(helm_root), "--user-schema", "1", "--yes"]
    )
    helm_config = load_config(helm_root / "sftpwarden.yaml")
    write_config(
        helm_root / "sftpwarden.yaml",
        helm_config.model_copy(
            update={
                "deploy": helm_config.deploy.model_copy(update={"target": DeployTarget.KUBERNETES}),
                "kubernetes": helm_config.kubernetes.model_copy(
                    update={"mode": KubernetesMode.HELM}
                ),
                "provider": helm_config.provider.model_copy(update={"user_schema": 2}),
            }
        ),
    )

    helm_upgrade = runner.invoke(app, ["helm", "upgrade", "--context", "helm-schema", "--yes"])

    assert kube_apply.exit_code == 0, kube_apply.output
    assert "Migrated provider schema v1 -> v2" in kube_apply.output
    assert "Backup:" in kube_apply.output
    assert "schema_version: 2" in (kube_root / "users.yaml").read_text(encoding="utf-8")
    assert helm_upgrade.exit_code == 0, helm_upgrade.output
    assert "Migrated provider schema v1 -> v2" in helm_upgrade.output
    assert "Backup:" in helm_upgrade.output
    assert "schema_version: 2" in (helm_root / "users.yaml").read_text(encoding="utf-8")
    assert any(command[0] == "kubectl" for command in run_calls)
    assert any(command[0] == "helm" for command in run_calls)


def test_key_schema_migration_confirmation_and_import_entry_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeService:
        def list_users(self) -> ProviderUsers:
            return ProviderUsers(
                schema_version=1,
                users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
            )

    user_commands.confirm_key_schema_migration(
        FakeService(),  # type: ignore[arg-type]
        "key rotate",
        yes=False,
        dry_run=True,
    )
    user_commands.confirm_key_schema_migration(
        FakeService(),  # type: ignore[arg-type]
        "key rotate",
        yes=True,
        dry_run=False,
    )
    monkeypatch.setattr(user_commands.Confirm, "ask", lambda *_args, **_kwargs: False)

    with pytest.raises(user_commands.typer.Exit):
        user_commands.confirm_key_schema_migration(
            FakeService(),  # type: ignore[arg-type]
            "key rotate",
            yes=False,
            dry_run=False,
        )

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    multi_dir = tmp_path / "multi"
    multi_dir.mkdir()
    (multi_dir / "one.pub").write_text(TEST_KEY, encoding="utf-8")
    (multi_dir / "two.pub").write_text(SECOND_TEST_KEY, encoding="utf-8")

    with pytest.raises(Exception, match="No .pub files"):
        user_commands.key_import_entries(empty_dir, explicit_name=None)
    with pytest.raises(Exception, match="--name can only be used"):
        user_commands.key_import_entries(multi_dir, explicit_name="prod")


def test_validate_json_reports_config_and_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(app, ["validate", "--config", str(root / "sftpwarden.yaml"), "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["valid"]
    assert data["project"] == "dev"
    assert data["provider"] == "yaml"
    assert data["provider_path"] == str(root / "users.yaml")


def test_doctor_json_reports_checks() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])
    data = json.loads(result.output)
    sync_binary = "scp" if platform.system() == "Windows" else "rsync"

    assert result.exit_code == 0, result.output
    assert {check["name"] for check in data["checks"]} == {
        "docker",
        "ssh",
        sync_binary,
        "kubectl",
        "helm",
    }
    assert all("required_for" in check for check in data["checks"])


def test_global_config_commands_show_and_update_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()

    show_result = runner.invoke(app, ["config", "show", "--json"])
    default_before = runner.invoke(app, ["config", "default-provider"])
    update_result = runner.invoke(app, ["config", "default-provider", "csv"])
    default_after = runner.invoke(app, ["config", "default-provider"])

    assert show_result.exit_code == 0, show_result.output
    assert json.loads(show_result.output)["defaults"]["remote_storage"] == "local-sync"
    assert default_before.exit_code == 0, default_before.output
    assert default_before.output.strip() == "yaml"
    assert update_result.exit_code == 0, update_result.output
    assert "csv" in update_result.output
    assert default_after.output.strip() == "csv"


def test_context_registry_commands_cover_show_list_default_clear_and_remove(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    add_result = runner.invoke(app, ["context", "add", "qa", "--root", str(root), "--yes"])
    list_result = runner.invoke(app, ["context", "ls", "--json"])
    show_result = runner.invoke(app, ["context", "show", "--name", "qa"])
    default_result = runner.invoke(app, ["context", "default", "qa"])
    use_result = runner.invoke(app, ["context", "use", "dev"])
    clear_result = runner.invoke(app, ["context", "clear"])
    remove_result = runner.invoke(app, ["context", "remove", "qa", "--yes"])
    registry = load_registry()

    assert add_result.exit_code == 0, add_result.output
    assert json.loads(list_result.output)["contexts"]["qa"]["root"] == str(root)
    assert json.loads(show_result.output)["name"] == "qa"
    assert default_result.exit_code == 0, default_result.output
    assert use_result.exit_code == 0, use_result.output
    assert clear_result.exit_code == 0, clear_result.output
    assert remove_result.exit_code == 0, remove_result.output
    assert registry.default is None
    assert "qa" not in registry.contexts


def test_context_ls_prunes_manually_deleted_context_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sftpwarden.services.context_cleanup.shutil.which", lambda _name: None)
    stale_root = tmp_path / "stale-project"
    live_root = tmp_path / "live-project"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "stale", "--root", str(stale_root), "--yes"]).exit_code == 0
    assert runner.invoke(app, ["init", "live", "--root", str(live_root), "--yes"]).exit_code == 0
    shutil.rmtree(stale_root)

    result = runner.invoke(app, ["context", "ls", "--json"])
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert set(data["contexts"]) == {"live"}
    assert load_registry().default == "live"


def test_context_remove_remote_keeps_remote_unless_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "prod"
    project.mkdir()
    write_config(project / "sftpwarden.yaml", default_project_config("prod"))
    entry = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="~/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    calls: list[bool] = []

    def fake_remove_context_with_cleanup(name: str, *, delete_remote: bool = False):
        calls.append(delete_remote)
        registry = load_registry()
        registry.contexts.pop(name)
        registry.default = None
        return type(
            "Report",
            (),
            {
                "removed_local_root": project,
                "local_runtime_messages": [],
                "remote_messages": [],
                "watcher_message": None,
            },
        )()

    monkeypatch.setattr(
        context_commands,
        "remove_context_with_cleanup",
        fake_remove_context_with_cleanup,
    )
    runner = CliRunner()

    keep_remote = runner.invoke(app, ["context", "remove", "prod", "--yes"])
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    delete_remote = runner.invoke(app, ["context", "remove", "prod", "--yes", "--delete-remote"])
    save_registry(ContextRegistry(default="prod", contexts={"prod": entry}))
    interactive_remote = runner.invoke(app, ["context", "remove", "prod"], input="y\ny\n")

    assert keep_remote.exit_code == 0, keep_remote.output
    assert delete_remote.exit_code == 0, delete_remote.output
    assert interactive_remote.exit_code == 0, interactive_remote.output
    assert calls == [False, True, True]
    assert "Remote project files were left untouched" in keep_remote.output


def test_info_compose_refresh_and_sync_json_commands(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    def fake_refresh_context(entry, *, dry_run=False):
        return f"{entry.name} dry_run={dry_run}"

    monkeypatch.setattr(core_commands, "refresh_context", fake_refresh_context)
    monkeypatch.setattr(
        core_commands,
        "derive_watch_targets",
        lambda: [WatchTarget("prod", root / "users.yaml", "/opt/sftpwarden/users.yaml")],
    )

    info_result = runner.invoke(app, ["info", "--json"])
    compose_result = runner.invoke(
        app, ["compose", "--config", str(root / "sftpwarden.yaml"), "--write"]
    )
    refresh_result = runner.invoke(app, ["refresh", "--json", "--dry-run"])
    sync_result = runner.invoke(app, ["sync", "--json", "--dry-run"])

    assert json.loads(info_result.output)["name"] == "dev"
    assert compose_result.exit_code == 0, compose_result.output
    assert (root / "docker-compose.yml").exists()
    assert json.loads(refresh_result.output) == {
        "dry_run": True,
        "targets": [{"context": "dev", "result": "dev dry_run=True"}],
    }
    assert json.loads(sync_result.output)["targets"][0]["remote_path"] == (
        "/opt/sftpwarden/users.yaml"
    )


def test_runtime_cli_commands_use_runtime_services(monkeypatch) -> None:
    runner = CliRunner()
    user = SFTPUser(
        username="alice",
        password_hash="$6$rounds=500000$saltstring$hashvalue",  # noqa: S106
    )
    plan = RuntimePlan(
        fingerprint="abc123",
        actions=[
            RuntimeAction(
                action="create",
                username="alice",
                uid=10000,
                gid=10000,
                reason="new user",
            )
        ],
        resolved_users=[ResolvedUser(spec=user, uid=10000, gid=10000)],
    )
    sync_calls: list[str] = []

    monkeypatch.setattr(runtime_commands, "apply_once", lambda config, force=False: "applied")
    monkeypatch.setattr(
        runtime_commands,
        "load_runtime_inputs",
        lambda config: (
            load_config("examples/yaml/sftpwarden.yaml"),
            ProviderUsers(users=[user]),
            RuntimeState(users={}),
        ),
    )
    monkeypatch.setattr(runtime_commands, "build_runtime_plan", lambda *_args: plan)
    monkeypatch.setattr(runtime_commands, "run_sync_loop", lambda config: sync_calls.append(config))

    refresh_result = runner.invoke(app, ["runtime", "refresh", "--config", "custom.yaml"])
    plan_result = runner.invoke(app, ["runtime", "plan", "--config", "custom.yaml", "--json"])
    sync_result = runner.invoke(app, ["runtime", "sync", "--config", "custom.yaml"])

    assert refresh_result.exit_code == 0, refresh_result.output
    assert "applied" in refresh_result.output
    assert json.loads(plan_result.output)["actions"][0]["username"] == "alice"
    assert sync_result.exit_code == 0, sync_result.output
    assert sync_calls == ["custom.yaml"]


def test_user_add_hashes_plaintext_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "bob",
            "--password",
            "correct horse battery staple",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))
    bob = provider["users"][0]

    assert result.exit_code == 0, result.output
    assert bob["username"] == "bob"
    assert bob["password_hash"].startswith("$6$")
    assert "correct horse battery staple" not in (root / "users.yaml").read_text(encoding="utf-8")


def test_user_add_public_key_does_not_prompt_for_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "keyonly",
            "--public-key",
            TEST_KEY,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"][0]["username"] == "keyonly"
    assert provider["users"][0]["keys"][0]["public_key"] == TEST_KEY
    assert "Password" not in result.output


def test_user_add_accepts_existing_password_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    password_hash = "$6$rounds=500000$saltstring$hashvalue"

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "carol",
            "--password-hash",
            password_hash,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"][0]["password_hash"] == password_hash


def test_user_add_stores_comment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "erin",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--comment",
            "Finance dropbox",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"][0]["comment"] == "Finance dropbox"


def test_user_show_accepts_config_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "heidi",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    result = runner.invoke(
        app,
        ["user", "show", "heidi", "--config", str(root / "sftpwarden.yaml")],
    )
    data = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert data["username"] == "heidi"


def test_user_add_runs_real_refresh_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    calls: list[bool] = []

    def fake_refresh_context(entry, *, dry_run=False):
        calls.append(dry_run)
        return f"refreshed {entry.name}"

    monkeypatch.setattr(cli_workflows, "refresh_context", fake_refresh_context)

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "erin",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [False]
    assert "refreshed dev" in result.output


def test_user_update_changes_uid_gid_and_upload_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    password_hash = "$6$rounds=500000$saltstring$hashvalue"
    runner.invoke(
        app,
        [
            "user",
            "add",
            "frank",
            "--password-hash",
            password_hash,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    result = runner.invoke(
        app,
        [
            "user",
            "update",
            "frank",
            "--uid",
            "12001",
            "--gid",
            "12002",
            "--upload-dir",
            "dropbox",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))
    frank = provider["users"][0]

    assert result.exit_code == 0, result.output
    assert frank["uid"] == 12001
    assert frank["gid"] == 12002
    assert frank["upload_dir"] == "dropbox"


def test_user_update_comment_only_does_not_refresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "grace",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    calls: list[bool] = []

    def fake_refresh_context(entry, *, dry_run=False):
        calls.append(dry_run)
        return f"refreshed {entry.name}"

    monkeypatch.setattr(cli_workflows, "refresh_context", fake_refresh_context)

    result = runner.invoke(
        app,
        [
            "user",
            "update",
            "grace",
            "--comment",
            "Archive account",
            "--context",
            "dev",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert calls == []
    assert provider["users"][0]["comment"] == "Archive account"


def test_user_add_rejects_password_and_hash_together(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "dana",
            "--password",
            "correct horse battery staple",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 1
    assert "Use either --password or --password-hash" in result.output


def test_user_remove_can_delete_user_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev-project"
    runner = CliRunner()
    runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            "$6$rounds=500000$saltstring$hashvalue",
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    data_file = root / "data" / "alice" / "upload" / "payload.txt"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("payload", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "user",
            "remove",
            "alice",
            "--context",
            "dev",
            "--delete-files",
            "--yes",
            "--no-refresh",
        ],
    )

    provider = yaml.safe_load((root / "users.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert provider["users"] == []
    assert not (root / "data" / "alice").exists()
    assert "Deleted data directory for alice" in result.output
