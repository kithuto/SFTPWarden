from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from sftpwarden.cli_commands.app import provider_app, provider_keys_app, provider_schema_app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.config import write_config
from sftpwarden.services.provider_transfer import (
    ProviderMutationResult,
    copy_provider_users,
    export_provider_users,
    import_provider_users,
    infer_format,
    resolve_provider_context,
    serialize_users,
)
from sftpwarden.users.schemas import migrate_provider_users, user_schema
from sftpwarden.utils.console import console, print_success
from sftpwarden.utils.errors import ProviderError, SFTPWardenError
from sftpwarden.utils.files import write_private_text


@provider_app.command("export")
def provider_export(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    output: Annotated[str | None, typer.Option("--output", "-o")] = None,
    format_name: Annotated[str | None, typer.Option("--format")] = None,
) -> None:
    """Export users from a provider.

    Parameters
    ----------
    context
        Optional context name.
    config
        Optional config path.
    output
        Optional output file.
    format_name
        Optional output format.
    """
    try:
        fmt = infer_format(output, format_name)
        _entry, text = export_provider_users(
            context_name=context,
            config_path=config,
            output=output,
            fmt=fmt,
        )
        if output:
            print_success(f"Exported provider users to [bold]{output}[/bold].")
            return
        console.file.write(text)
        console.file.flush()
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_app.command("import")
def provider_import(
    input_path: Annotated[str, typer.Option("--input", "-i")],
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    format_name: Annotated[str | None, typer.Option("--format")] = None,
    merge: Annotated[bool, typer.Option("--merge")] = False,
    replace: Annotated[bool, typer.Option("--replace")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Import users into a provider.

    Parameters
    ----------
    input_path
        Input provider transfer file.
    context
        Destination context.
    format_name
        Optional input format.
    merge
        Whether to merge users into the destination provider.
    replace
        Whether to replace the destination provider users.
    dry_run
        Whether to avoid writing.
    json_output
        Whether to emit JSON.
    no_refresh
        Whether to skip runtime refresh.
    """
    try:
        result = import_provider_users(
            context_name=context,
            input_path=input_path,
            mode=resolve_transfer_mode(merge=merge, replace=replace),
            fmt=infer_format(input_path, format_name),
            dry_run=dry_run,
            no_refresh=no_refresh,
        )
        print_provider_mutation_result(result, dry_run=dry_run, json_output=json_output)
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_app.command("copy")
def provider_copy(
    from_context: Annotated[str, typer.Option("--from-context")],
    to_context: Annotated[str, typer.Option("--to-context")],
    merge: Annotated[bool, typer.Option("--merge")] = False,
    replace: Annotated[bool, typer.Option("--replace")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Copy users between providers.

    Parameters
    ----------
    from_context
        Source context.
    to_context
        Destination context.
    merge
        Whether to merge users into the destination provider.
    replace
        Whether to replace the destination provider users.
    dry_run
        Whether to avoid writing.
    json_output
        Whether to emit JSON.
    no_refresh
        Whether to skip runtime refresh.
    """
    try:
        result = copy_provider_users(
            from_context=from_context,
            to_context=to_context,
            mode=resolve_transfer_mode(merge=merge, replace=replace),
            dry_run=dry_run,
            no_refresh=no_refresh,
        )
        print_provider_mutation_result(result, dry_run=dry_run, json_output=json_output)
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_schema_app.command("show")
def provider_schema_show(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the active provider user schema."""
    try:
        _entry, project_config, provider = resolve_provider_context(
            context_name=context,
            config_path=config,
        )
        users = provider.read()
        data = {
            "configured_user_schema": project_config.provider.user_schema,
            "provider_user_schema": users.schema_version,
            "user_count": len(users.users),
            "provider_type": project_config.provider.type.value,
        }
        if json_output:
            print_json(data)
            return
        console.print(
            f"Provider schema v[bold]{users.schema_version}[/bold] "
            f"({len(users.users)} user(s), "
            f"configured default v{project_config.provider.user_schema})."
        )
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_schema_app.command("migrate")
def provider_schema_migrate(
    to_schema: Annotated[int, typer.Option("--to")] = 2,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    backup: Annotated[bool, typer.Option("--backup/--no-backup")] = True,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Migrate provider user schema."""
    try:
        result = migrate_provider_schema(
            to_schema=to_schema,
            context=context,
            config=config,
            backup=backup,
            yes=yes,
            dry_run=dry_run,
        )
        if json_output:
            print_json(result)
            return
        if result["changed"]:
            action = "Would migrate" if dry_run else "Migrated"
            print_success(
                f"{action} provider schema v{result['from_schema']} -> v{result['to_schema']}."
            )
            if result["backup_path"]:
                console.print(f"Backup: {result['backup_path']}")
        else:
            print_success(f"Provider already uses schema v{result['to_schema']}.")
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_keys_app.command("migrate")
def provider_keys_migrate(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    backup: Annotated[bool, typer.Option("--backup/--no-backup")] = True,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Migrate anonymous public_keys to named keys schema v2."""
    provider_schema_migrate(
        to_schema=2,
        context=context,
        config=config,
        backup=backup,
        yes=yes,
        dry_run=dry_run,
        json_output=json_output,
    )


def resolve_transfer_mode(*, merge: bool, replace: bool):
    """Resolve transfer mode flags.

    Parameters
    ----------
    merge
        Whether merge mode was requested.
    replace
        Whether replace mode was requested.

    Returns
    -------
    str
        Transfer mode.
    """
    if merge == replace:
        raise ProviderError("Use exactly one of --merge or --replace.")
    return "merge" if merge else "replace"


def migrate_provider_schema(
    *,
    to_schema: int,
    context: str | None,
    config: str | None,
    backup: bool,
    yes: bool,
    dry_run: bool,
) -> dict[str, object]:
    """Migrate the selected provider to a schema version."""
    target_schema = user_schema(to_schema)
    entry, project_config, provider = resolve_provider_context(
        context_name=context,
        config_path=config,
    )
    users = provider.read()
    if users.schema_version == target_schema.version:
        return {
            "changed": False,
            "from_schema": users.schema_version,
            "to_schema": target_schema.version,
            "backup_path": None,
            "dry_run": dry_run,
        }
    migrated = migrate_provider_users(users, to_version=target_schema.version)
    backup_path = None
    if backup and not dry_run:
        backup_path = write_provider_backup(entry.root, users)
    if dry_run:
        return {
            "changed": True,
            "from_schema": users.schema_version,
            "to_schema": target_schema.version,
            "backup_path": None,
            "dry_run": True,
            "users": len(migrated.users),
        }
    if not yes and not typer.confirm(
        f"Migrate provider users to schema v{target_schema.version}?",
        default=False,
    ):
        raise typer.Exit(1)
    provider.write(migrated)
    if entry.config and project_config.provider.user_schema != target_schema.version:
        write_config(
            entry.config,
            project_config.model_copy(
                update={
                    "provider": project_config.provider.model_copy(
                        update={"user_schema": target_schema.version}
                    )
                }
            ),
        )
    return {
        "changed": True,
        "from_schema": users.schema_version,
        "to_schema": target_schema.version,
        "backup_path": str(backup_path) if backup_path else None,
        "dry_run": False,
        "users": len(migrated.users),
    }


def write_provider_backup(project_root: Path | None, users) -> Path:
    """Write a logical YAML backup for provider migration."""
    root = Path(project_root) if project_root is not None else Path.cwd()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = root / ".sftpwarden" / "backups" / f"provider-users-{timestamp}.yaml"
    write_private_text(backup_path, serialize_users(users, "yaml"))
    return backup_path


def print_provider_mutation_result(
    result: ProviderMutationResult, *, dry_run: bool, json_output: bool
) -> None:
    """Print provider mutation output.

    Parameters
    ----------
    result
        Mutation result.
    dry_run
        Whether the operation was a dry-run.
    json_output
        Whether to emit JSON.
    """
    data = {
        "dry_run": dry_run,
        "source_count": result.source_count,
        "destination_count": result.destination_count,
        "changed": result.changed,
        "runtime_changed": result.runtime_changed,
        "synced": result.sync_output is not None,
        "refreshed": result.refresh_output is not None,
        "deploy_required": result.deploy_required,
        "manual_action": result.manual_action,
    }
    if json_output:
        print_json(data)
        return
    action = "Would update" if dry_run and result.changed else "Updated"
    if not result.changed:
        action = "No provider changes detected"
    print_success(
        f"{action}. Source users: [bold]{result.source_count}[/bold], "
        f"destination users: [bold]{result.destination_count}[/bold]."
    )
    if result.sync_output:
        console.print(result.sync_output)
    if result.refresh_output:
        console.print(result.refresh_output)
    if result.deploy_required:
        console.print(
            "Kubernetes provider changes are saved locally. Run `sftpwarden deploy` "
            "to sync YAML/CSV users into the provider PVC."
        )
    if result.manual_action:
        console.print(result.manual_action)
