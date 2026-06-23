from __future__ import annotations

from typing import Annotated

import typer

from sftpwarden.utils._version import get_version
from sftpwarden.utils.console import console

app = typer.Typer(help="Container-native SFTP gateway powered by OpenSSH.")
config_app = typer.Typer(
    help="Global CLI configuration.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
context_app = typer.Typer(
    help="Context registry management.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
runtime_app = typer.Typer(help="Runtime-only commands used inside the container.")
user_app = typer.Typer(help="Manage users in mutable providers.")
provider_app = typer.Typer(help="Import, export, and copy provider users.")
watcher_app = typer.Typer(help="Watcher management.")

app.add_typer(config_app, name="config")
app.add_typer(context_app, name="context")
app.add_typer(runtime_app, name="runtime")
app.add_typer(user_app, name="user")
app.add_typer(provider_app, name="provider")
app.add_typer(watcher_app, name="watcher")


def version_callback(value: bool) -> None:
    """Handle the eager ``--version`` option.

    Parameters
    ----------
    value
        Whether the version flag was provided.
    """
    if value:
        console.print(f"SFTPWarden {get_version()}")
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
    """Configure root CLI options.

    Parameters
    ----------
    version
        Eager version flag handled by Typer before command dispatch.
    """
    _ = version
