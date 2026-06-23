from __future__ import annotations

import json
import types
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sftpwarden.cli_commands.provider as provider_commands
import sftpwarden.services.provider_transfer as transfer_services
from sftpwarden.cli import app
from sftpwarden.config import ProviderType, default_project_config
from sftpwarden.contexts import ContextEntry, ContextRegistry, remote_context, save_registry
from sftpwarden.services.provider_transfer import (
    ProviderMutationResult,
    deserialize_users,
    export_provider_users,
    infer_format,
    read_context_users,
    serialize_users,
    sync_provider_file_if_needed,
    write_provider_users,
)
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


def test_provider_transfer_serialization_and_service_mutations(
    local_project_factory: Callable[..., tuple[Path, ContextEntry]],
    memory_provider_factory: Callable[[ProviderUsers], object],
    monkeypatch: pytest.MonkeyPatch,
    user_factory: Callable[..., SFTPUser],
) -> None:
    """Provider transfer serializes snapshots and writes with safe refresh semantics."""
    users = ProviderUsers(users=[user_factory("alice", comment="Finance inbox")])
    yaml_text = serialize_users(users, "yaml")
    csv_text = serialize_users(users, "csv")

    assert deserialize_users(yaml_text, "yaml").users == users.users
    assert deserialize_users(csv_text, "csv").users == users.users
    assert infer_format("users.json") == "json"
    assert infer_format("users.csv") == "csv"
    assert infer_format(None) == "yaml"
    with pytest.raises(ProviderError, match="yaml, csv, or json"):
        infer_format(None, "toml")

    root, entry = local_project_factory()
    output = root / "users.csv"
    _entry, text = export_provider_users(
        context_name="dev",
        config_path=None,
        output=str(output),
        fmt="csv",
    )
    assert "username" in text
    assert output.read_text(encoding="utf-8").startswith("username,")

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
    with pytest.raises(ProviderError, match="no local provider configuration"):
        read_context_users(context_name="archive")

    refresh_calls: list[ContextEntry] = []
    monkeypatch.setattr(
        transfer_services,
        "refresh_context",
        lambda refreshed_entry: refresh_calls.append(refreshed_entry) or "refreshed",
    )
    provider = memory_provider_factory(ProviderUsers(users=[]))
    result = write_provider_users(
        entry=entry,
        config=default_project_config("dev"),
        provider=provider,
        source_users=users,
        mode="replace",
        dry_run=False,
        no_refresh=False,
    )
    assert result.refresh_output == "refreshed"
    assert refresh_calls == [entry]

    dry_provider = memory_provider_factory(ProviderUsers(users=[]))
    dry_result = write_provider_users(
        entry=entry,
        config=default_project_config("dev"),
        provider=dry_provider,
        source_users=users,
        mode="replace",
        dry_run=True,
        no_refresh=False,
    )
    assert dry_result.changed
    assert dry_provider.writes == []  # type: ignore

    no_change = write_provider_users(
        entry=entry,
        config=default_project_config("dev"),
        provider=memory_provider_factory(users),
        source_users=users,
        mode="merge",
        dry_run=False,
        no_refresh=False,
    )
    assert not no_change.changed

    remote_sync = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=root,
        remote_root="/opt/sftpwarden",
        remote_only=False,
        ssh_key=None,
        critical=True,
    )
    monkeypatch.setattr(
        transfer_services,
        "editable_sync_target",
        lambda _entry, _config: types.SimpleNamespace(
            local_path=root / "users.yaml",
            remote_path="/opt/sftpwarden/users.yaml",
        ),
    )
    monkeypatch.setattr(
        transfer_services,
        "sync_target",
        lambda _entry, _local, _remote: "synced",
    )
    assert sync_provider_file_if_needed(remote_sync, default_project_config("dev")) == "synced"
    monkeypatch.setattr(transfer_services, "editable_sync_target", lambda _entry, _config: None)
    assert sync_provider_file_if_needed(remote_sync, default_project_config("dev")) is None


def test_provider_transfer_cli_output_and_error_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Provider transfer CLI wrappers print streams, summaries, JSON and errors."""
    runner = CliRunner()
    monkeypatch.setattr(
        provider_commands,
        "export_provider_users",
        lambda **_kwargs: (object(), "users: []\n"),
    )
    assert runner.invoke(app, ["provider", "export"]).output == "users: []\n"
    result = runner.invoke(app, ["provider", "export", "--output", str(tmp_path / "users.yaml")])
    assert result.exit_code == 0
    assert "Exported provider users" in result.output

    mutation = ProviderMutationResult(
        source_count=1,
        destination_count=2,
        changed=True,
        runtime_changed=True,
        refresh_output="refreshed",
        sync_output="synced",
    )
    monkeypatch.setattr(provider_commands, "import_provider_users", lambda **_kwargs: mutation)
    result = runner.invoke(app, ["provider", "import", "--input", "users.json", "--merge"])
    assert result.exit_code == 0
    assert "Updated" in result.output
    assert "synced" in result.output
    assert "refreshed" in result.output

    monkeypatch.setattr(provider_commands, "copy_provider_users", lambda **_kwargs: mutation)
    result = runner.invoke(
        app,
        [
            "provider",
            "copy",
            "--from-context",
            "dev",
            "--to-context",
            "prod",
            "--replace",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["dry_run"]

    no_change = ProviderMutationResult(0, 0, False, False)
    provider_commands.print_provider_mutation_result(no_change, dry_run=False, json_output=False)
    with pytest.raises(ProviderError, match="exactly one"):
        provider_commands.resolve_transfer_mode(merge=False, replace=False)

    monkeypatch.setattr(
        provider_commands,
        "copy_provider_users",
        lambda **_kwargs: (_ for _ in ()).throw(ProviderError("copy failed")),
    )
    result = runner.invoke(
        app,
        ["provider", "copy", "--from-context", "dev", "--to-context", "prod", "--merge"],
    )
    assert result.exit_code == 1
    assert "copy failed" in result.output

    monkeypatch.setattr(
        provider_commands,
        "export_provider_users",
        lambda **_kwargs: (_ for _ in ()).throw(ProviderError("export failed")),
    )
    result = runner.invoke(app, ["provider", "export"])
    assert result.exit_code == 1
    assert "export failed" in result.output

    monkeypatch.setattr(
        provider_commands,
        "import_provider_users",
        lambda **_kwargs: (_ for _ in ()).throw(ProviderError("import failed")),
    )
    result = runner.invoke(app, ["provider", "import", "--input", "users.json", "--merge"])
    assert result.exit_code == 1
    assert "import failed" in result.output


def test_provider_transfer_copy_import_export_and_comment_only_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    test_password_hash: str,
) -> None:
    """Provider transfer copies users and skips refresh for comment-only imports."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    runner.invoke(app, ["init", "source", "--root", str(source), "--yes"])
    runner.invoke(app, ["init", "destination", "--root", str(destination), "--yes"])
    runner.invoke(
        app,
        [
            "user",
            "add",
            "alice",
            "--password-hash",
            test_password_hash,
            "--context",
            "source",
            "--no-refresh",
        ],
    )

    copy_result = runner.invoke(
        app,
        [
            "provider",
            "copy",
            "--from-context",
            "source",
            "--to-context",
            "destination",
            "--replace",
            "--no-refresh",
            "--json",
        ],
    )
    export_result = runner.invoke(
        app,
        ["provider", "export", "--context", "destination", "--format", "json"],
    )

    assert copy_result.exit_code == 0, copy_result.output
    assert json.loads(copy_result.output)["changed"]
    assert export_result.exit_code == 0, export_result.output
    exported = json.loads(export_result.output)
    assert exported["users"][0]["username"] == "alice"

    input_path = tmp_path / "users.json"
    exported["users"][0]["comment"] = "comment only"
    input_path.write_text(json.dumps(exported), encoding="utf-8")
    refresh_calls: list[object] = []
    monkeypatch.setattr(
        transfer_services,
        "refresh_context",
        lambda entry: refresh_calls.append(entry) or "refreshed",
    )

    import_result = transfer_services.import_provider_users(
        context_name="destination",
        input_path=str(input_path),
        mode="merge",
        fmt="json",
        dry_run=False,
        no_refresh=False,
    )

    assert import_result.changed
    assert not import_result.runtime_changed
    assert refresh_calls == []
