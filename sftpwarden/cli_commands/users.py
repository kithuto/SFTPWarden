from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.prompt import Confirm
from rich.table import Table

from sftpwarden.cli_commands.app import app, user_app, user_key_app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.cli_commands.prompts import prompt_password_hash
from sftpwarden.services.cli_workflows import print_refresh_after_user_change
from sftpwarden.services.users import UserService
from sftpwarden.users.schemas import (
    KEY_LIFECYCLE,
    first_schema_with_capability,
    user_schema,
)
from sftpwarden.utils.console import console, print_success
from sftpwarden.utils.errors import SFTPWardenError


@app.command("users")
def users_list(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List users in the selected context.

    Parameters
    ----------
    context
        Optional context name.
    config
        Optional direct config path.
    json_output
        Whether to emit users as JSON.
    """
    try:
        service = UserService(context_name=context, config_path=config)
        users = service.list_users()
        if json_output:
            print_json(users.model_dump_json(indent=2))
            return
        table = Table(
            title=f"Users in {service.context.name}",
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
        )
        table.add_column("Username", style="bold")
        table.add_column("Keys", justify="right")
        table.add_column("UID", justify="right")
        table.add_column("GID", justify="right")
        table.add_column("Comment")
        table.add_column("Disabled", justify="center")
        for user in users.users:
            table.add_row(
                user.username,
                str(len(user.key_objects())),
                str(user.uid or ""),
                str(user.gid or ""),
                user.comment or "",
                str(user.disabled),
            )
        console.print(table)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("show")
def user_show(
    username: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Show one user as JSON.

    Parameters
    ----------
    username
        Username to look up.
    context
        Optional context name.
    config
        Optional direct config path.
    """
    try:
        user = UserService(context_name=context, config_path=config).show_user(username)
        print_json(user.model_dump_json(indent=2))
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("create")
@user_app.command("add", hidden=True)
def user_add(
    username: str,
    public_key: Annotated[list[str] | None, typer.Option("--public-key")] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Plaintext password to hash before saving."),
    ] = None,
    password_hash: Annotated[str | None, typer.Option("--password-hash")] = None,
    upload_dir: Annotated[str, typer.Option("--upload-dir")] = "upload",
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    uid: Annotated[int | None, typer.Option("--uid")] = None,
    gid: Annotated[int | None, typer.Option("--gid")] = None,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Add a user to the selected provider.

    Parameters
    ----------
    username
        Username to create.
    public_key
        Public keys to assign.
    password
        Plaintext password to hash before storing.
    password_hash
        Precomputed password hash.
    upload_dir
        User upload directory relative to the chroot.
    comment
        Optional operator note.
    uid
        Optional explicit UID.
    gid
        Optional explicit GID.
    context
        Optional context name.
    no_refresh
        Whether to skip automatic refresh after mutation.
    """
    try:
        service = UserService(context_name=context)
        resolved_password_hash = prompt_password_hash(
            password=password,
            password_hash=password_hash,
            prompt_if_missing=service.config.auth.allow_password and not public_key,
        )
        service.add_user(
            username=username,
            public_keys=[load_public_key(value) for value in public_key or []],
            password_hash=resolved_password_hash,
            upload_dir=upload_dir,
            comment=comment,
            uid=uid,
            gid=gid,
        )
        print_success(f"Saved user [bold]{username}[/bold].")
        if not no_refresh:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("update")
def user_update(
    username: str,
    public_key: Annotated[list[str] | None, typer.Option("--public-key")] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Plaintext password to hash before saving."),
    ] = None,
    password_hash: Annotated[str | None, typer.Option("--password-hash")] = None,
    upload_dir: Annotated[str | None, typer.Option("--upload-dir")] = None,
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    uid: Annotated[int | None, typer.Option("--uid")] = None,
    gid: Annotated[int | None, typer.Option("--gid")] = None,
    disabled: Annotated[bool | None, typer.Option("--disabled/--enabled")] = None,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Update an existing provider user.

    Parameters
    ----------
    username
        Username to update.
    public_key
        Replacement public keys.
    password
        Plaintext password to hash before storing.
    password_hash
        Replacement precomputed password hash.
    upload_dir
        Replacement upload directory.
    comment
        Replacement operator note.
    uid
        Replacement explicit UID.
    gid
        Replacement explicit GID.
    disabled
        Whether the user should be disabled or enabled.
    context
        Optional context name.
    no_refresh
        Whether to skip automatic refresh after runtime-affecting mutation.
    """
    try:
        service = UserService(context_name=context)
        resolved_password_hash = prompt_password_hash(
            password=password,
            password_hash=password_hash,
        )
        result = service.update_user(
            username,
            public_keys=[load_public_key(value) for value in public_key] if public_key else None,
            password_hash=resolved_password_hash,
            upload_dir=upload_dir,
            comment=comment,
            uid=uid,
            gid=gid,
            disabled=disabled,
        )
        print_success(f"Updated user [bold]{username}[/bold].")
        if not no_refresh and result.runtime_changed:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("disable")
def user_disable(
    username: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Disable a provider user."""
    try:
        service = UserService(context_name=context)
        result = service.set_user_disabled(username, disabled=True)
        print_success(f"Disabled user [bold]{username}[/bold].")
        if not no_refresh and result.runtime_changed:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("enable")
def user_enable(
    username: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Enable a provider user."""
    try:
        service = UserService(context_name=context)
        result = service.set_user_disabled(username, disabled=False)
        print_success(f"Enabled user [bold]{username}[/bold].")
        if not no_refresh and result.runtime_changed:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("remove")
def user_remove(
    username: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
    delete_files: Annotated[
        bool,
        typer.Option(
            "--delete-files",
            "--force-delete-files",
            help="Delete the user's data directory after removing the provider user.",
        ),
    ] = False,
) -> None:
    """Remove a user from the selected provider.

    Parameters
    ----------
    username
        Username to remove.
    context
        Optional context name.
    yes
        Whether to skip confirmation.
    no_refresh
        Whether to skip automatic refresh after mutation.
    delete_files
        Whether to delete the user's runtime data directory.
    """
    try:
        message = (
            f"Remove user {username} and permanently delete all user files?"
            if delete_files
            else f"Remove user {username}? User data will not be deleted."
        )
        if not yes and not Confirm.ask(
            message,
            default=False,
        ):
            raise typer.Exit(1)
        service = UserService(context_name=context)
        service.remove_user(username)
        print_success(f"Removed user [bold]{username}[/bold].")
        if not no_refresh:
            print_refresh_after_user_change(service.context)
        if delete_files:
            console.print(service.delete_user_files(username))
    except SFTPWardenError as exc:
        handle_error(exc)


@user_key_app.command("list")
def user_key_list(
    username: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """List a user's SSH keys."""
    try:
        service = UserService(context_name=context, config_path=config)
        keys = service.list_user_keys(username)
        table = Table(
            title=f"Keys for {username}",
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
        )
        table.add_column("Name", style="bold")
        table.add_column("Fingerprint")
        table.add_column("Active", justify="center")
        table.add_column("Disabled", justify="center")
        table.add_column("Expires")
        table.add_column("Comment")
        for key in keys:
            table.add_row(
                key.name,
                key.fingerprint or "",
                str(key.is_active()),
                str(key.disabled),
                key.expires_at.isoformat() if key.expires_at else "",
                key.comment or "",
            )
        console.print(table)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_key_app.command("show")
def user_key_show(
    username: str,
    key_name: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Show one user key as JSON."""
    try:
        key = UserService(context_name=context, config_path=config).show_user_key(
            username, key_name
        )
        print_json(key.model_dump_json(indent=2))
    except SFTPWardenError as exc:
        handle_error(exc)


@user_key_app.command("add")
def user_key_add(
    username: str,
    key_name: str,
    public_key: Annotated[str, typer.Option("--public-key")],
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Add a key to a user."""
    try:
        service = UserService(context_name=context, config_path=config)
        result = service.add_user_key(
            username,
            key_name=key_name,
            public_key=load_public_key(public_key),
            comment=comment,
            dry_run=dry_run,
        )
        report_key_mutation("Added", username, key_name, result)
        if not no_refresh and result.runtime_changed and not dry_run:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_key_app.command("remove")
def user_key_remove(
    username: str,
    key_name: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Remove one key from a user."""
    try:
        if (
            not yes
            and not dry_run
            and not Confirm.ask(f"Remove key {key_name} from user {username}?", default=False)
        ):
            raise typer.Exit(1)
        service = UserService(context_name=context, config_path=config)
        result = service.remove_user_key(username, key_name, dry_run=dry_run)
        report_key_mutation("Removed", username, key_name, result)
        if not no_refresh and result.runtime_changed and not dry_run:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_key_app.command("disable")
def user_key_disable(
    username: str,
    key_name: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Disable one named key."""
    mutate_advanced_key(
        "Disabled",
        username,
        key_name,
        context=context,
        config=config,
        yes=yes,
        dry_run=dry_run,
        no_refresh=no_refresh,
        operation=lambda service: service.disable_user_key(
            username,
            key_name,
            disabled=True,
            allow_migration=True,
            dry_run=dry_run,
        ),
    )


@user_key_app.command("enable")
def user_key_enable(
    username: str,
    key_name: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Enable one named key."""
    mutate_advanced_key(
        "Enabled",
        username,
        key_name,
        context=context,
        config=config,
        yes=yes,
        dry_run=dry_run,
        no_refresh=no_refresh,
        operation=lambda service: service.disable_user_key(
            username,
            key_name,
            disabled=False,
            allow_migration=True,
            dry_run=dry_run,
        ),
    )


@user_key_app.command("rename")
def user_key_rename(
    username: str,
    old_name: str,
    new_name: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Rename one key."""
    mutate_advanced_key(
        "Renamed",
        username,
        old_name,
        context=context,
        config=config,
        yes=yes,
        dry_run=dry_run,
        no_refresh=no_refresh,
        operation=lambda service: service.rename_user_key(
            username,
            old_name,
            new_name,
            allow_migration=True,
            dry_run=dry_run,
        ),
    )


@user_key_app.command("rotate")
def user_key_rotate(
    username: str,
    key_name: str,
    public_key: Annotated[str, typer.Option("--public-key")],
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Rotate one key's public key."""
    mutate_advanced_key(
        "Rotated",
        username,
        key_name,
        context=context,
        config=config,
        yes=yes,
        dry_run=dry_run,
        no_refresh=no_refresh,
        operation=lambda service: service.rotate_user_key(
            username,
            key_name,
            public_key=load_public_key(public_key),
            allow_migration=True,
            dry_run=dry_run,
        ),
    )


@user_key_app.command("expire")
def user_key_expire(
    username: str,
    key_name: str,
    at: Annotated[str, typer.Option("--at")],
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Set one key expiration."""
    mutate_advanced_key(
        "Expired",
        username,
        key_name,
        context=context,
        config=config,
        yes=yes,
        dry_run=dry_run,
        no_refresh=no_refresh,
        operation=lambda service: service.expire_user_key(
            username,
            key_name,
            expires_at=at,
            allow_migration=True,
            dry_run=dry_run,
        ),
    )


@user_key_app.command("import")
def user_key_import(
    username: str,
    from_dir: Annotated[Path, typer.Option("--from-dir", exists=True, file_okay=False)],
    name: Annotated[str | None, typer.Option("--name")] = None,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Import public keys from a directory."""
    try:
        key_files = key_import_entries(from_dir, explicit_name=name)
        service = UserService(context_name=context, config_path=config)
        confirm_key_schema_migration(service, "key import", yes=yes, dry_run=dry_run)
        result = service.import_user_keys(
            username,
            key_files,
            allow_migration=True,
            dry_run=dry_run,
        )
        report_key_mutation("Imported", username, f"{len(key_files)} key(s)", result)
        if not no_refresh and result.runtime_changed and not dry_run:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


def mutate_advanced_key(
    label: str,
    username: str,
    key_name: str,
    *,
    context: str | None,
    config: str | None,
    yes: bool,
    dry_run: bool,
    no_refresh: bool,
    operation,
) -> None:
    """Run a key operation that requires schema v2 metadata."""
    try:
        service = UserService(context_name=context, config_path=config)
        confirm_key_schema_migration(service, label.lower(), yes=yes, dry_run=dry_run)
        result = operation(service)
        report_key_mutation(label, username, key_name, result)
        if not no_refresh and result.runtime_changed and not dry_run:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)


def confirm_key_schema_migration(
    service: UserService,
    operation: str,
    *,
    yes: bool,
    dry_run: bool,
) -> None:
    """Confirm v1 to v2 migration when an operation needs named-key metadata."""
    users = service.list_users()
    schema = user_schema(users.schema_version)
    if schema.supports(KEY_LIFECYCLE):
        return
    target_version = first_schema_with_capability(
        KEY_LIFECYCLE,
        from_version=users.schema_version,
    )
    if dry_run:
        console.print(
            "Dry run: would migrate provider user schema "
            f"from v{users.schema_version} to v{target_version} for {operation}."
        )
        return
    if not yes and not Confirm.ask(
        (
            f"{operation} requires schema v{target_version}. "
            f"Migrate this provider from v{users.schema_version} to v{target_version}?"
        ),
        default=False,
    ):
        raise typer.Exit(1)


def report_key_mutation(label: str, username: str, key_name: str, result) -> None:
    """Print a standard key mutation message."""
    prefix = "Dry run: would" if result.dry_run else label
    migration = " after migrating provider to schema v2" if result.schema_migrated else ""
    console.print(
        f"{prefix} key [bold]{key_name}[/bold] for user [bold]{username}[/bold]{migration}."
    )


def key_import_entries(from_dir: Path, *, explicit_name: str | None) -> list[tuple[str, str]]:
    """Return key names and public key contents from a directory."""
    paths = sorted(path for path in from_dir.iterdir() if path.is_file() and path.suffix == ".pub")
    if not paths:
        raise SFTPWardenError(f"No .pub files found in {from_dir}.")
    if explicit_name and len(paths) != 1:
        raise SFTPWardenError("--name can only be used when --from-dir contains one .pub file.")
    return [(explicit_name or path.stem, load_public_key(str(path))) for path in paths]


def load_public_key(value: str) -> str:
    """Load a public key from a path or return the literal key text."""
    if "\n" not in value:
        path = Path(value).expanduser()
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return value
