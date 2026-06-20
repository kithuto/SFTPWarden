from __future__ import annotations

from typing import Annotated

import typer
from rich import box
from rich.prompt import Confirm
from rich.table import Table

from sftpwarden.cli_commands.common import (
    app,
    handle_error,
    print_json,
    prompt_password_hash,
    user_app,
)
from sftpwarden.services.cli_workflows import print_refresh_after_user_change
from sftpwarden.services.users import UserService
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
                str(len(user.public_keys)),
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


@user_app.command("add")
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
            prompt_if_missing=service.config.auth.allow_password,
        )
        service.add_user(
            username=username,
            public_keys=public_key,
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
            public_keys=public_key,
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
