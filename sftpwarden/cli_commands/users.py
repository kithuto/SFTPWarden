from __future__ import annotations

from typing import Annotated

import typer
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
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError


@app.command("users")
def users_list(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        service = UserService(context_name=context, config_path=config)
        users = service.list_users()
        if json_output:
            print_json(users.model_dump_json(indent=2))
            return
        table = Table(title=f"Users in {service.context.name}")
        table.add_column("Username")
        table.add_column("Keys")
        table.add_column("UID")
        table.add_column("GID")
        table.add_column("Comment")
        table.add_column("Disabled")
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
        console.print(f"[green]Saved[/green] user [bold]{username}[/bold].")
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
        console.print(f"[green]Updated[/green] user [bold]{username}[/bold].")
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
) -> None:
    try:
        if not yes and not Confirm.ask(
            f"Remove user {username}? User data will not be deleted.", default=False
        ):
            raise typer.Exit(1)
        service = UserService(context_name=context)
        service.remove_user(username)
        console.print(f"[green]Removed[/green] user [bold]{username}[/bold].")
        if not no_refresh:
            print_refresh_after_user_change(service.context)
    except SFTPWardenError as exc:
        handle_error(exc)
