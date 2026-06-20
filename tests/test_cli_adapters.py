from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import sftpwarden.cli as cli_module
import sftpwarden.cli_commands.common as common_commands
import sftpwarden.cli_commands.config as config_commands
import sftpwarden.cli_commands.context as context_commands
import sftpwarden.cli_commands.core as core_commands
import sftpwarden.cli_commands.init as init_commands
import sftpwarden.cli_commands.runtime as runtime_commands
import sftpwarden.cli_commands.users as user_commands
import sftpwarden.cli_commands.watcher as watcher_commands
from sftpwarden.cli import app
from sftpwarden.config import (
    ProviderConfig,
    ProviderType,
    default_project_config,
    load_config,
    write_config,
)
from sftpwarden.contexts import (
    ContextRegistry,
    load_registry,
    local_context,
    save_registry,
)
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.watcher import WatcherInstallMode, WatchTarget

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


class FakeTyperContext:
    def __init__(self, args: list[str], *, invoked_subcommand: str | None = None) -> None:
        self.args = args
        self.invoked_subcommand = invoked_subcommand


def init_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CliRunner, Path]:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    runner = CliRunner()
    result = runner.invoke(app, ["init", "dev", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output
    return runner, root


def add_user(runner: CliRunner, username: str = "alice") -> None:
    result = runner.invoke(
        app,
        [
            "user",
            "add",
            username,
            "--password-hash",
            TEST_HASH,
            "--context",
            "dev",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_version_callback_and_module_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "app", lambda: calls.append("called"))

    common_commands.version_callback(False)
    with pytest.raises(typer.Exit):
        common_commands.version_callback(True)
    runpy.run_module("sftpwarden", run_name="__main__", alter_sys=True)

    assert calls == ["called"]


def test_prompt_password_hash_prompts_and_rejects_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["secret-one", "secret-two"])
    monkeypatch.setattr(common_commands.Prompt, "ask", lambda *_args, **_kwargs: next(answers))

    with pytest.raises(SFTPWardenError, match="Passwords do not match"):
        common_commands.prompt_password_hash(
            password=None, password_hash=None, prompt_if_missing=True
        )

    answers = iter(["secret-one", "secret-one"])
    monkeypatch.setattr(common_commands.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(
        common_commands,
        "resolve_password_hash",
        lambda *, password, password_hash: f"hash:{password or password_hash}",
    )

    assert (
        common_commands.prompt_password_hash(
            password=None, password_hash=None, prompt_if_missing=True
        )
        == "hash:secret-one"
    )


def test_config_callback_reads_updates_and_validates_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(tmp_path, monkeypatch)

    config_commands.config_value(FakeTyperContext([]))
    config_commands.config_value(FakeTyperContext([], invoked_subcommand="show"))
    with pytest.raises(typer.Exit):
        config_commands.config_value(FakeTyperContext(["server.port"]))
    with pytest.raises(typer.Exit):
        config_commands.config_value(FakeTyperContext(["server.port", "2201"]))
    with pytest.raises(typer.Exit) as usage_error:
        config_commands.config_value(FakeTyperContext(["a", "b", "c"]))

    registry = load_registry()
    config = load_config(Path(registry.contexts["dev"].config))
    assert config.server.port == 2201
    assert usage_error.value.exit_code == 1


def test_config_callback_renames_context_and_reports_invalid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(tmp_path, monkeypatch)

    with pytest.raises(typer.Exit):
        config_commands.config_value(FakeTyperContext(["project.name", "renamed"]))
    with pytest.raises(typer.Exit) as invalid_error:
        config_commands.config_value(FakeTyperContext(["server.port", "not-an-int"]))

    registry = load_registry()
    assert registry.default == "renamed"
    assert "renamed" in registry.contexts
    assert invalid_error.value.exit_code == 1


def test_config_commands_cover_errors_and_global_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)
    no_config = local_context("remoteish", tmp_path / "remoteish", ProviderType.YAML)
    no_config.config = ""
    save_registry(
        ContextRegistry(
            default="dev",
            contexts={
                "dev": load_registry().contexts["dev"],
                "remoteish": no_config,
                "existing": local_context("existing", tmp_path / "existing", ProviderType.YAML),
            },
        )
    )

    with pytest.raises(typer.Exit) as callback_error:
        config_commands.config_value(
            FakeTyperContext(["server.port"]), context="remoteish", config=None
        )
    config_commands.rename_context_for_project_name("missing", "new")
    with pytest.raises(SFTPWardenError, match="already exists"):
        config_commands.rename_context_for_project_name("dev", "existing")
    with pytest.raises(SFTPWardenError, match="no local"):
        config_commands.update_project_config_value(
            "server.port", None, context="remoteish", config=None
        )

    show_text = runner.invoke(app, ["config", "show"])
    show_json = runner.invoke(app, ["config", "show", "--json"])
    default_provider = runner.invoke(app, ["config", "default-provider"])
    set_provider = runner.invoke(app, ["config", "default-provider", "csv"])
    invalid_provider = runner.invoke(app, ["config", "default-provider", "bad"])
    dynamic_missing = runner.invoke(app, ["config", "server.port", "--context", "missing"])
    dynamic_invalid = runner.invoke(app, ["config", "server.port", "not-an-int"])

    monkeypatch.setattr(
        config_commands,
        "global_config_data",
        lambda: (_ for _ in ()).throw(SFTPWardenError("global broken")),
    )
    show_error = runner.invoke(app, ["config", "show"])

    assert callback_error.value.exit_code == 1
    assert "defaults:" in show_text.output
    assert json.loads(show_json.output)["default_provider"] == "yaml"
    assert "yaml" in default_provider.output
    assert "csv" in set_provider.output
    assert invalid_provider.exit_code == 1
    assert dynamic_missing.exit_code == 1
    assert dynamic_invalid.exit_code == 1
    assert show_error.exit_code == 1


def test_context_callback_reads_updates_and_validates_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(tmp_path, monkeypatch)

    context_commands.context_value(FakeTyperContext([]))
    context_commands.context_value(FakeTyperContext([], invoked_subcommand="show"))
    with pytest.raises(typer.Exit):
        context_commands.context_value(FakeTyperContext(["root"]))
    with pytest.raises(typer.Exit):
        context_commands.context_value(FakeTyperContext(["critical", "true"]))
    with pytest.raises(typer.Exit) as usage_error:
        context_commands.context_value(FakeTyperContext(["a", "b", "c"]))

    assert load_registry().contexts["dev"].critical is True
    assert usage_error.value.exit_code == 1


def test_context_callback_reports_invalid_field_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(tmp_path, monkeypatch)

    with pytest.raises(typer.Exit) as invalid_error:
        context_commands.context_value(FakeTyperContext(["port", "not-an-int"]))
    monkeypatch.setattr(
        context_commands,
        "update_context_field",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad context value")),
    )
    with pytest.raises(typer.Exit) as value_error:
        context_commands.context_value(FakeTyperContext(["critical", "true"]))

    assert invalid_error.value.exit_code == 1
    assert value_error.value.exit_code == 1


def test_context_callback_show_and_add_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)

    with pytest.raises(typer.Exit) as invalid_error:
        context_commands.context_value(FakeTyperContext(["missing.path"]))

    with pytest.raises(typer.Exit) as show_missing:
        context_commands.context_show("missing")
    add_missing_root = runner.invoke(
        app, ["context", "add", "qa", "--root", str(tmp_path / "missing")]
    )

    assert invalid_error.value.exit_code == 1
    assert show_missing.value.exit_code == 1
    assert add_missing_root.exit_code == 1


def test_context_field_aliases_and_remote_root_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    runner = CliRunner()
    result = runner.invoke(
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
        ],
    )
    assert result.exit_code == 0, result.output

    assert context_commands.normalize_context_field("remote_root") == "remote.remote_root"
    updated = context_commands.update_context_field(
        "prod",
        "remote.remote_root",
        "/srv/sftpwarden",
        remote_url=None,
        root=None,
        remote_user=None,
        port=None,
        remote_root=None,
        remote_only=False,
        delete_old_root=False,
        yes=True,
    )

    entry = load_registry().contexts[updated]
    assert entry.remote.remote_root == "/srv/sftpwarden"
    assert entry.remote.remote_config == "/srv/sftpwarden/sftpwarden.yaml"


def test_context_commands_show_list_and_manage_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)

    list_result = runner.invoke(app, ["context", "ls"])
    current_result = runner.invoke(app, ["context", "current"])
    show_result = runner.invoke(app, ["context", "show"])
    rename_result = runner.invoke(app, ["context", "rename", "dev", "renamed"])
    clear_result = runner.invoke(app, ["context", "clear"])
    missing_current = runner.invoke(app, ["context", "current"])
    default_missing = runner.invoke(app, ["context", "default", "missing"])
    remove_cancelled = runner.invoke(app, ["context", "remove", "renamed"], input="n\n")
    remove_missing = runner.invoke(app, ["context", "remove", "missing", "--yes"])
    rename_missing = runner.invoke(app, ["context", "rename", "missing", "new"])

    assert list_result.exit_code == 0, list_result.output
    assert "SFTPWarden contexts" in list_result.output
    assert "dev" in current_result.output
    assert json.loads(show_result.output)["name"] == "dev"
    assert "Renamed" in rename_result.output
    assert load_registry().contexts["renamed"].name == "renamed"
    assert "Default context cleared" in clear_result.output
    assert missing_current.exit_code == 1
    assert default_missing.exit_code == 1
    assert remove_cancelled.exit_code == 1
    assert remove_missing.exit_code == 1
    assert rename_missing.exit_code == 1


def test_context_dynamic_field_commands_read_update_and_report_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    runner = CliRunner()
    init_result = runner.invoke(
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
        ],
    )
    assert init_result.exit_code == 0, init_result.output

    read_result = runner.invoke(app, ["context", "host", "--context", "prod"])
    update_result = runner.invoke(
        app, ["context", "host", "sftp-prod.example.com", "--context", "prod"]
    )
    invalid_result = runner.invoke(app, ["context", "port", "not-a-port", "--context", "prod"])
    missing_result = runner.invoke(app, ["context", "host", "--context", "missing"])

    assert "example.com" in read_result.output
    assert update_result.exit_code == 0, update_result.output
    assert load_registry().contexts["prod"].remote.host == "sftp-prod.example.com"
    assert invalid_result.exit_code == 1
    assert missing_result.exit_code == 1


def test_context_add_local_remote_and_confirmation_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)
    other_root = tmp_path / "other"
    other_root.mkdir()
    write_config(other_root / "sftpwarden.yaml", default_project_config("qa"))
    checked_hosts: list[str] = []
    monkeypatch.setattr(
        context_commands,
        "verify_remote_runtime_requirements",
        lambda remote: checked_hosts.append(remote.host),
    )

    local_result = runner.invoke(app, ["context", "add", "qa", "--root", str(other_root)])
    remote_result = runner.invoke(
        app,
        [
            "context",
            "add",
            "staging",
            "deploy@example.com:/opt/sftpwarden",
            "--root",
            str(other_root),
            "--critical",
            "--yes",
        ],
    )
    production_cancelled = runner.invoke(app, ["context", "add", "prod"], input="n\n")
    invalid_provider = runner.invoke(app, ["context", "add", "bad", "--provider", "unknown"])

    assert local_result.exit_code == 0, local_result.output
    assert remote_result.exit_code == 0, remote_result.output
    assert checked_hosts == ["example.com"]
    assert production_cancelled.exit_code == 1
    assert invalid_provider.exit_code == 1


def test_context_update_helpers_cover_root_and_type_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    old_root = tmp_path / "old"
    old_root.mkdir()
    write_config(old_root / "sftpwarden.yaml", default_project_config("dev"))
    registry = ContextRegistry(
        default="dev",
        contexts={
            "dev": local_context("dev", old_root, ProviderType.YAML),
            "existing": local_context("existing", tmp_path / "existing", ProviderType.YAML),
        },
    )
    save_registry(registry)
    monkeypatch.setattr(context_commands.Confirm, "ask", lambda *_args, **_kwargs: False)

    with pytest.raises(SFTPWardenError, match="Unknown context"):
        context_commands.update_context_field(
            "missing",
            "critical",
            "true",
            remote_url=None,
            root=None,
            remote_user=None,
            port=None,
            remote_root=None,
            remote_only=False,
            delete_old_root=False,
            yes=True,
        )
    with pytest.raises(SFTPWardenError, match="already exists"):
        context_commands.rename_context_and_project(registry, "dev", "existing")

    entry = registry.contexts["dev"]
    assert (
        context_commands.migrate_context_root(entry, str(old_root), delete_old_root=False, yes=True)
        is entry
    )
    with pytest.raises(typer.Exit):
        context_commands.migrate_context_root(
            entry, str(tmp_path / "cancelled"), delete_old_root=False, yes=False
        )

    missing_root_entry = local_context("missing-root", tmp_path / "missing", ProviderType.YAML)
    migrated = context_commands.migrate_context_root(
        missing_root_entry, str(tmp_path / "created"), delete_old_root=False, yes=True
    )
    assert Path(migrated.root).exists()

    assert (
        context_commands.convert_context_type(
            entry,
            "local",
            remote_url=None,
            root=None,
            remote_user=None,
            port=None,
            remote_root=None,
            remote_only=False,
            yes=True,
        )
        is entry
    )
    with pytest.raises(SFTPWardenError, match="local or remote"):
        context_commands.convert_context_type(
            entry,
            "other",
            remote_url=None,
            root=None,
            remote_user=None,
            port=None,
            remote_root=None,
            remote_only=False,
            yes=True,
        )

    answers = iter(["example.com", "deploy", "/srv/sftpwarden"])
    monkeypatch.setattr(context_commands.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    remote_entry = context_commands.convert_context_type(
        entry,
        "remote",
        remote_url=None,
        root=str(old_root),
        remote_user=None,
        port=2202,
        remote_root=None,
        remote_only=False,
        yes=True,
    )
    assert remote_entry.remote.host == "example.com"
    assert remote_entry.remote.port == 2202

    with pytest.raises(typer.Exit):
        context_commands.convert_context_type(
            remote_entry,
            "local",
            remote_url=None,
            root=None,
            remote_user=None,
            port=None,
            remote_root=None,
            remote_only=False,
            yes=False,
        )
    with pytest.raises(SFTPWardenError, match="no remote settings"):
        context_commands.update_remote_root(entry, "/srv/sftpwarden", yes=True)
    with pytest.raises(typer.Exit):
        context_commands.update_remote_root(remote_entry, "/srv/sftpwarden", yes=False)


def test_users_list_json_and_table_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)
    add_user(runner)

    table_result = runner.invoke(app, ["users"])
    json_result = runner.invoke(app, ["users", "--json"])
    data = json.loads(json_result.output)

    assert table_result.exit_code == 0, table_result.output
    assert "alice" in table_result.output
    assert data["users"][0]["username"] == "alice"


def test_core_commands_render_human_outputs_and_dry_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, root = init_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        core_commands, "refresh_context", lambda entry, *, dry_run=False: "refreshed"
    )
    monkeypatch.setattr(
        core_commands,
        "derive_watch_targets",
        lambda: [WatchTarget("prod", root / "users.yaml", "/remote/users.yaml")],
    )
    poll_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        core_commands,
        "poll_watch",
        lambda *, interval_seconds, dry_run: poll_calls.append((interval_seconds, dry_run)),
    )

    info_result = runner.invoke(app, ["info"])
    validate_result = runner.invoke(app, ["validate", "--config", str(root / "sftpwarden.yaml")])
    compose_result = runner.invoke(app, ["compose", "--config", str(root / "sftpwarden.yaml")])
    plan_result = runner.invoke(app, ["plan"])
    refresh_result = runner.invoke(app, ["refresh", "--dry-run"])
    sync_result = runner.invoke(app, ["sync", "--dry-run"])
    watch_result = runner.invoke(app, ["watch", "--interval", "1", "--dry-run"])
    doctor_result = runner.invoke(app, ["doctor"])

    assert info_result.exit_code == 0, info_result.output
    assert "Context dev" in info_result.output
    assert "Valid config" in validate_result.output
    assert "services:" in compose_result.output
    assert "No deploy-level configuration changes detected." in plan_result.output
    assert "refreshed" in refresh_result.output
    assert "Dry run only; no files synced." in sync_result.output
    assert watch_result.exit_code == 0, watch_result.output
    assert poll_calls == [(1, True)]
    assert "SFTPWarden doctor" in doctor_result.output


def test_core_commands_json_write_and_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, root = init_project(tmp_path, monkeypatch)
    config_path = root / "sftpwarden.yaml"
    entry = load_registry().contexts["dev"]
    loaded = load_config(config_path)
    loaded.project.name = "renamed"
    loaded.provider = ProviderConfig(type=ProviderType.CSV, path="users.csv")
    write_config(config_path, loaded)
    (root / "users.csv").write_text(
        "username,public_keys,password_hash,uid,gid,upload_dir,comment,disabled\n",
        encoding="utf-8",
    )
    config_reasons = core_commands.deploy_config_change_reasons(entry, loaded)
    (root / loaded.docker.compose_file).unlink()
    missing_compose_reasons = core_commands.deploy_config_change_reasons(entry, loaded)

    info_json = runner.invoke(app, ["info", "--json"])
    validate_json = runner.invoke(app, ["validate", "--config", str(config_path), "--json"])
    compose_write = runner.invoke(app, ["compose", "--config", str(config_path), "--write"])
    plan_json = runner.invoke(app, ["plan", "--json"])
    remote_only = context_commands.remote_context(
        name="archive",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=None,
        remote_root="~/sftpwarden",
        remote_only=True,
        ssh_key=None,
        critical=True,
    )
    save_registry(ContextRegistry(default="archive", contexts={"archive": remote_only}))
    plan_no_local = runner.invoke(app, ["plan"])
    sync_text = runner.invoke(app, ["sync"])
    doctor_json = runner.invoke(app, ["doctor", "--json"])
    validate_error = runner.invoke(app, ["validate", "--config", str(tmp_path / "missing.yaml")])
    compose_error = runner.invoke(app, ["compose", "--config", str(tmp_path / "missing.yaml")])

    monkeypatch.setattr(
        core_commands,
        "resolve_context",
        lambda **_kwargs: (_ for _ in ()).throw(SFTPWardenError("context broken")),
    )
    info_error = runner.invoke(app, ["info"])
    plan_error = runner.invoke(app, ["plan"])
    deploy_error = runner.invoke(app, ["deploy"])
    monkeypatch.setattr(
        core_commands,
        "resolve_refresh_targets",
        lambda **_kwargs: (_ for _ in ()).throw(SFTPWardenError("refresh broken")),
    )
    refresh_error = runner.invoke(app, ["refresh"])
    monkeypatch.setattr(
        core_commands,
        "derive_watch_targets",
        lambda: (_ for _ in ()).throw(SFTPWardenError("sync broken")),
    )
    sync_error = runner.invoke(app, ["sync"])
    monkeypatch.setattr(
        core_commands,
        "poll_watch",
        lambda **_kwargs: (_ for _ in ()).throw(SFTPWardenError("watch broken")),
    )
    watch_error = runner.invoke(app, ["watch"])

    assert any("project.name differs" in reason for reason in config_reasons)
    assert any("provider type differs" in reason for reason in config_reasons)
    assert any("docker-compose.yml differs" in reason for reason in config_reasons)
    assert any("docker-compose.yml is missing" in reason for reason in missing_compose_reasons)
    assert json.loads(info_json.output)["name"] == "dev"
    assert json.loads(validate_json.output)["valid"] is True
    assert "Wrote" in compose_write.output
    assert json.loads(plan_json.output)["deploy_config_changed"] is True
    assert plan_no_local.exit_code == 1
    assert sync_text.exit_code == 0
    assert json.loads(doctor_json.output)["checks"]
    assert validate_error.exit_code == 1
    assert compose_error.exit_code == 1
    assert info_error.exit_code == 1
    assert plan_error.exit_code == 1
    assert deploy_error.exit_code == 1
    assert refresh_error.exit_code == 1
    assert sync_error.exit_code == 1
    assert watch_error.exit_code == 1


def test_core_deploy_critical_confirmation_can_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "prod", "--root", str(root), "--critical", "--yes"])
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(app, ["deploy"], input="n\n")

    assert result.exit_code == 1


def test_init_command_prompt_and_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()

    conflict = runner.invoke(
        app,
        [
            "init",
            "prod",
            "--remote",
            "deploy@example.com:/opt/sftpwarden",
            "--remote-url",
            "deploy@example.com:/srv/sftpwarden",
        ],
    )
    production_cancelled = runner.invoke(app, ["init", "prod"], input="n\n")

    first = runner.invoke(app, ["init", "dev", "--root", str(tmp_path / "dev"), "--yes"])
    second = runner.invoke(app, ["init", "qa", "--root", str(tmp_path / "qa"), "--yes"])
    overwrite_cancelled = runner.invoke(
        app, ["init", "dev", "--root", str(tmp_path / "dev")], input="n\n"
    )

    assert conflict.exit_code == 1
    assert production_cancelled.exit_code == 1
    assert first.exit_code == 0, first.output
    assert "Using global default provider" in second.output
    assert overwrite_cancelled.exit_code == 1


def test_init_direct_helpers_cover_prompts_sql_and_remote_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SFTPWardenError, match="requires --dsn"):
        init_commands.init_project_config(
            "dev",
            ProviderType.MYSQL,
            dsn=None,
            query=None,
            table="sftp_users",
            yes=True,
        )
    answers_for_dsn = iter(["mysql://user:pass@db/sftp"])
    monkeypatch.setattr(
        init_commands.Prompt, "ask", lambda *_args, **_kwargs: next(answers_for_dsn)
    )
    prompted_sql = init_commands.init_project_config(
        "sql",
        ProviderType.MYSQL,
        dsn=None,
        query=None,
        table="sftp_users",
        yes=False,
    )
    assert prompted_sql.provider.dsn == "mysql://user:pass@db/sftp"

    sql_config = default_project_config("dev", ProviderType.MYSQL, dsn="mysql://db/sftp")

    class FakeSqlProvider:
        def __init__(self, exists: bool) -> None:
            self.exists = exists
            self.created = False

        def table_exists(self) -> bool:
            return self.exists

        def create_table(self) -> None:
            self.created = True

    existing = FakeSqlProvider(True)
    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: existing)
    init_commands.ensure_sql_table_for_init(tmp_path, sql_config, create_table=None, yes=False)
    assert existing.created is False

    missing = FakeSqlProvider(False)
    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: missing)
    monkeypatch.setattr(init_commands.Confirm, "ask", lambda *_args, **_kwargs: True)
    init_commands.ensure_sql_table_for_init(tmp_path, sql_config, create_table=None, yes=False)
    assert missing.created is True

    missing_default = FakeSqlProvider(False)
    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: missing_default)
    init_commands.ensure_sql_table_for_init(tmp_path, sql_config, create_table=None, yes=True)
    assert missing_default.created is True

    aborting = FakeSqlProvider(False)
    monkeypatch.setattr(init_commands, "provider_from_config", lambda *_args: aborting)
    with pytest.raises(SFTPWardenError, match="does not exist"):
        init_commands.ensure_sql_table_for_init(tmp_path, sql_config, create_table=False, yes=False)

    answers = iter(
        [
            "prompted-dev",
            str(tmp_path / "interactive-root"),
            str(tmp_path / "remote-root"),
            "example.com",
            str(tmp_path / "remote-local-root"),
        ]
    )
    monkeypatch.setattr(init_commands.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(init_commands.Confirm, "ask", lambda *_args, **_kwargs: False)

    init_commands.init(
        context_name=None,
        context=None,
        provider=None,
        root=None,
        remote=None,
        remote_url=None,
        dsn=None,
        query=None,
        table="sftp_users",
        create_table=None,
        host=None,
        remote_user="deploy",
        port=None,
        remote_root=None,
        ssh_key=None,
        watcher_mode=None,
        remote_only=False,
        skip_checks=True,
        critical=False,
        yes=False,
    )
    assert (tmp_path / "interactive-root" / "sftpwarden.yaml").exists()

    checked_hosts: list[str] = []
    monkeypatch.setattr(
        init_commands,
        "verify_remote_runtime_requirements",
        lambda remote: checked_hosts.append(remote.host),
    )
    init_commands.init_remote_context(
        name="staging",
        provider=None,
        root=None,
        remote_url=None,
        dsn=None,
        query=None,
        table="sftp_users",
        create_table=None,
        host=None,
        remote_user="deploy",
        port=None,
        remote_root=None,
        ssh_key=None,
        watcher_mode=None,
        remote_only=False,
        skip_checks=False,
        critical=False,
        yes=False,
    )
    assert checked_hosts == ["example.com"]

    with pytest.raises(typer.Exit):
        init_commands.init_remote_context(
            name="prod",
            provider=None,
            root=str(tmp_path / "prod"),
            remote_url="deploy@example.com:/opt/sftpwarden",
            dsn=None,
            query=None,
            table="sftp_users",
            create_table=None,
            host=None,
            remote_user=None,
            port=None,
            remote_root=None,
            ssh_key=None,
            watcher_mode=None,
            remote_only=False,
            skip_checks=True,
            critical=False,
            yes=False,
        )


def test_user_update_runtime_change_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)
    add_user(runner)
    calls: list[str] = []

    monkeypatch.setattr(
        user_commands,
        "print_refresh_after_user_change",
        lambda context: calls.append(context.name),
    )

    result = runner.invoke(app, ["user", "update", "alice", "--uid", "12000"])

    assert result.exit_code == 0, result.output
    assert calls == ["dev"]


def test_user_commands_cover_error_no_refresh_and_delete_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, root = init_project(tmp_path, monkeypatch)
    add_user(runner)
    refresh_calls: list[str] = []
    monkeypatch.setattr(
        user_commands,
        "print_refresh_after_user_change",
        lambda context: refresh_calls.append(context.name),
    )

    show_result = runner.invoke(app, ["user", "show", "alice"])
    list_error = runner.invoke(app, ["users", "--context", "missing"])
    show_missing = runner.invoke(app, ["user", "show", "missing"])
    add_invalid = runner.invoke(
        app,
        [
            "user",
            "add",
            "BadUser",
            "--password-hash",
            TEST_HASH,
        ],
    )
    add_refreshed = runner.invoke(
        app,
        [
            "user",
            "add",
            "bob",
            "--password-hash",
            TEST_HASH,
        ],
    )
    update_comment = runner.invoke(app, ["user", "update", "bob", "--comment", "friendly"])
    update_missing = runner.invoke(app, ["user", "update", "missing", "--uid", "12000"])
    remove_deleted = runner.invoke(app, ["user", "remove", "bob", "--yes", "--delete-files"])
    remove_missing = runner.invoke(app, ["user", "remove", "missing", "--yes"])

    assert json.loads(show_result.output)["username"] == "alice"
    assert list_error.exit_code == 1
    assert show_missing.exit_code == 1
    assert add_invalid.exit_code == 1
    assert add_refreshed.exit_code == 0, add_refreshed.output
    assert update_comment.exit_code == 0, update_comment.output
    assert update_missing.exit_code == 1
    assert remove_deleted.exit_code == 0, remove_deleted.output
    assert remove_missing.exit_code == 1
    assert "No data directory" in remove_deleted.output
    assert refresh_calls == ["dev", "dev"]
    assert (root / "users.yaml").exists()


def test_user_remove_can_be_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, _root = init_project(tmp_path, monkeypatch)
    add_user(runner)

    result = runner.invoke(app, ["user", "remove", "alice"], input="n\n")

    assert result.exit_code == 1


def test_watcher_status_install_and_uninstall_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        watcher_commands,
        "watcher_status_data",
        lambda: {"installed": True, "mode": "systemd"},
    )
    monkeypatch.setattr(watcher_commands, "watcher_status_text", lambda: "watcher ready")
    monkeypatch.setattr(watcher_commands, "installed_watcher_mode", lambda: None)
    monkeypatch.setattr(
        watcher_commands,
        "install_watcher",
        lambda **kwargs: f"install {kwargs['mode']} dry={kwargs['dry_run']}",
    )
    monkeypatch.setattr(
        watcher_commands,
        "uninstall_watcher",
        lambda *, dry_run=False: f"uninstall dry={dry_run}",
    )

    status_text = runner.invoke(app, ["watcher", "status"])
    status_json = runner.invoke(app, ["watcher", "status", "--json"])
    install_result = runner.invoke(app, ["watcher", "install", "--watcher", "docker", "--dry-run"])
    uninstall_result = runner.invoke(app, ["watcher", "uninstall", "--dry-run"])

    assert "watcher ready" in status_text.output
    assert json.loads(status_json.output)["mode"] == "systemd"
    assert "install docker dry=True" in install_result.output
    assert "uninstall dry=True" in uninstall_result.output


def test_watcher_install_replacement_confirmation_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    install_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        watcher_commands,
        "installed_watcher_mode",
        lambda: WatcherInstallMode.SYSTEMD,
    )

    def fake_install_watcher(**kwargs: Any) -> str:
        install_calls.append(kwargs)
        return "installed"

    monkeypatch.setattr(watcher_commands, "install_watcher", fake_install_watcher)

    cancelled = runner.invoke(app, ["watcher", "install", "--watcher", "docker"], input="n\n")
    accepted = runner.invoke(app, ["watcher", "install", "--watcher", "docker"], input="y\n")

    assert cancelled.exit_code == 1
    assert accepted.exit_code == 0, accepted.output
    assert install_calls[0]["yes"] is True


def test_watcher_install_and_uninstall_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(watcher_commands, "installed_watcher_mode", lambda: None)
    monkeypatch.setattr(
        watcher_commands,
        "install_watcher",
        lambda **_kwargs: (_ for _ in ()).throw(SFTPWardenError("install broken")),
    )
    install_error = runner.invoke(app, ["watcher", "install"])

    monkeypatch.setattr(
        watcher_commands,
        "uninstall_watcher",
        lambda *, dry_run=False: (_ for _ in ()).throw(SFTPWardenError("uninstall broken")),
    )
    uninstall_cancelled = runner.invoke(app, ["watcher", "uninstall"], input="n\n")
    uninstall_error = runner.invoke(app, ["watcher", "uninstall", "--yes"])

    assert install_error.exit_code == 1
    assert uninstall_cancelled.exit_code == 1
    assert uninstall_error.exit_code == 1


def test_runtime_commands_render_and_report_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    monkeypatch.setattr(runtime_commands, "apply_once", lambda config, *, force: "applied")
    monkeypatch.setattr(
        runtime_commands,
        "load_runtime_inputs",
        lambda config: (
            default_project_config("dev"),
            type("Users", (), {"users": []})(),
            type("State", (), {})(),
        ),
    )
    monkeypatch.setattr(
        runtime_commands,
        "build_runtime_plan",
        lambda *_args: type(
            "Plan",
            (),
            {
                "fingerprint": "abc",
                "changed": False,
                "actions": [],
                "summary": lambda self: "no changes",
            },
        )(),
    )
    sync_calls: list[str] = []
    monkeypatch.setattr(runtime_commands, "run_sync_loop", lambda config: sync_calls.append(config))

    refresh = runner.invoke(app, ["runtime", "refresh"])
    plan_text = runner.invoke(app, ["runtime", "plan"])
    plan_json = runner.invoke(app, ["runtime", "plan", "--json"])
    runtime_config = str(tmp_path / "config.yaml")
    sync = runner.invoke(app, ["runtime", "sync", "--config", runtime_config])

    monkeypatch.setattr(
        runtime_commands,
        "apply_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SFTPWardenError("apply broken")),
    )
    refresh_error = runner.invoke(app, ["runtime", "refresh"])
    monkeypatch.setattr(
        runtime_commands,
        "load_runtime_inputs",
        lambda *_args: (_ for _ in ()).throw(SFTPWardenError("plan broken")),
    )
    plan_error = runner.invoke(app, ["runtime", "plan"])
    monkeypatch.setattr(
        runtime_commands,
        "run_sync_loop",
        lambda *_args: (_ for _ in ()).throw(SFTPWardenError("sync broken")),
    )
    sync_error = runner.invoke(app, ["runtime", "sync"])

    assert "applied" in refresh.output
    assert "no changes" in plan_text.output
    assert json.loads(plan_json.output)["fingerprint"] == "abc"
    assert sync.exit_code == 0
    assert sync_calls == [runtime_config]
    assert refresh_error.exit_code == 1
    assert plan_error.exit_code == 1
    assert sync_error.exit_code == 1
