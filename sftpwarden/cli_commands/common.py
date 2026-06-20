from __future__ import annotations

import hmac
import json
from typing import Annotated, Any

import typer
from rich.prompt import Prompt
from rich.table import Table

from sftpwarden import __version__
from sftpwarden.runtime import RuntimePlan
from sftpwarden.security.passwords import resolve_password_hash
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError

app = typer.Typer(help="Container-native SFTP gateway powered by OpenSSH.")
config_app = typer.Typer(help="Global CLI configuration.")
context_app = typer.Typer(help="Context registry management.")
runtime_app = typer.Typer(help="Runtime-only commands used inside the container.")
user_app = typer.Typer(help="Manage users in mutable providers.")
watcher_app = typer.Typer(help="Watcher management.")

app.add_typer(config_app, name="config")
app.add_typer(context_app, name="context")
app.add_typer(runtime_app, name="runtime")
app.add_typer(user_app, name="user")
app.add_typer(watcher_app, name="watcher")


def handle_error(exc: SFTPWardenError) -> None:
    console.print(f"[bold red]Error:[/bold red] {exc.message}")
    if exc.suggestion:
        console.print(f"[yellow]Fix:[/yellow] {exc.suggestion}")
    raise typer.Exit(1)


def prompt_password_hash(
    *,
    password: str | None,
    password_hash: str | None,
    prompt_if_missing: bool = False,
) -> str | None:
    if password is not None and password_hash is not None:
        return resolve_password_hash(password=password, password_hash=password_hash)
    if password is None and password_hash is None and prompt_if_missing:
        first = Prompt.ask("Password", password=True)
        second = Prompt.ask("Repeat password", password=True)
        if not hmac.compare_digest(first, second):
            raise SFTPWardenError("Passwords do not match.")
        password = first
    return resolve_password_hash(password=password, password_hash=password_hash)


def runtime_plan_to_json(runtime_plan: RuntimePlan) -> str:
    return json.dumps(
        {
            "fingerprint": runtime_plan.fingerprint,
            "changed": runtime_plan.changed,
            "actions": [
                {
                    "action": action.action,
                    "username": action.username,
                    "uid": action.uid,
                    "gid": action.gid,
                    "reason": action.reason,
                }
                for action in runtime_plan.actions
            ],
        },
        indent=2,
        sort_keys=True,
    )


def print_json(data: Any) -> None:
    text = data if isinstance(data, str) else json.dumps(data, indent=2, sort_keys=True)
    console.file.write(text)
    console.file.write("\n")
    console.file.flush()


def print_runtime_plan(runtime_plan: RuntimePlan) -> None:
    if not runtime_plan.actions:
        return
    table = Table(title="Runtime sync actions")
    table.add_column("Action")
    table.add_column("Username")
    table.add_column("UID")
    table.add_column("GID")
    table.add_column("Reason")
    for action in runtime_plan.actions:
        table.add_row(
            action.action,
            action.username,
            str(action.uid or ""),
            str(action.gid or ""),
            action.reason,
        )
    console.print(table)


def version_callback(value: bool) -> None:
    if value:
        console.print(f"SFTPWarden {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    _ = version
