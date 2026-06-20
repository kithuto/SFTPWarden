from __future__ import annotations

from typing import Annotated

import typer
import yaml

from sftpwarden.cli_commands.common import (
    config_app,
    handle_error,
    print_json,
)
from sftpwarden.config import (
    ProviderType,
)
from sftpwarden.config.global_config import (
    global_config_data,
    load_global_config,
    save_global_config,
)
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError


@config_app.command("show")
def config_show(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        data = global_config_data()
        if json_output:
            print_json(data)
            return
        console.print(yaml.safe_dump(data, sort_keys=False))
    except SFTPWardenError as exc:
        handle_error(exc)


@config_app.command("default-provider")
def config_default_provider(provider: Annotated[str | None, typer.Argument()] = None) -> None:
    try:
        config = load_global_config()
        if provider is None:
            console.print(config.default_provider.value if config.default_provider else "yaml")
            return
        config.default_provider = ProviderType(provider)
        save_global_config(config)
        console.print(f"Default provider set to [bold]{config.default_provider.value}[/bold].")
    except (SFTPWardenError, ValueError) as exc:
        handle_error(exc if isinstance(exc, SFTPWardenError) else SFTPWardenError(str(exc)))
