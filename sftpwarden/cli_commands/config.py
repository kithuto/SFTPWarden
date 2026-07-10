from __future__ import annotations

from typing import Annotated, Any

import typer
import yaml

from sftpwarden.cli_commands.app import config_app
from sftpwarden.cli_commands.errors import cli_error_from_exception, handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.config import (
    ProviderType,
    SFTPWardenConfig,
    load_config,
    write_config,
)
from sftpwarden.config.global_config import (
    global_config_data,
    load_global_config,
    save_global_config,
)
from sftpwarden.contexts import ContextEntry, load_registry, resolve_context, save_registry
from sftpwarden.services.provider_schema import plan_provider_schema_reconciliation
from sftpwarden.utils.console import console, print_success
from sftpwarden.utils.constants import PROJECT_CONFIG_PATHS
from sftpwarden.utils.dotted import format_value, get_dotted, parse_cli_value, set_dotted
from sftpwarden.utils.errors import SFTPWardenError


@config_app.callback(invoke_without_command=True)
def config_value(
    ctx: typer.Context,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Read or update one value in the active project config.

    Parameters
    ----------
    ctx
        Typer context.
    context
        Optional context name.
    config
        Optional direct config path.
    """
    if ctx.invoked_subcommand is not None:
        return
    args = list(ctx.args)
    if not args:
        return
    if len(args) > 2:
        handle_error(SFTPWardenError("Usage: sftpwarden config <path> [value]"))
    path = args[0]
    value = args[1] if len(args) == 2 else None
    try:
        entry = resolve_context(config_path=config, context_name=context)
        if not entry.config:
            raise SFTPWardenError(
                f"Context {entry.name} has no local sftpwarden.yaml.",
                suggestion="Use a local or remote local-sync context, or pass --config.",
            )
        loaded = load_config(entry.config)
        data = loaded.model_dump(mode="json")
        if value is None:
            console.print(format_value(get_dotted(data, path)))
            raise typer.Exit()
        update_project_config_data(
            entry=entry,
            data=data,
            path=path,
            value=value,
            context_config_path=config,
            yes=yes,
        )
        raise typer.Exit()
    except SFTPWardenError as exc:
        handle_error(exc)
    except ValueError as exc:
        handle_error(cli_error_from_exception(exc))


def rename_context_for_project_name(old_name: str, new_name: str) -> None:
    """Rename a registry context after ``project.name`` changes.

    Parameters
    ----------
    old_name
        Previous context name.
    new_name
        New project/context name.
    """
    registry = load_registry()
    if old_name not in registry.contexts:
        return
    if new_name in registry.contexts and new_name != old_name:
        raise SFTPWardenError(f"Context already exists: {new_name}")
    entry = registry.contexts.pop(old_name)
    entry.name = new_name
    registry.contexts[new_name] = entry
    if registry.default == old_name:
        registry.default = new_name
    save_registry(registry)


def update_project_config_value(
    path: str,
    value: str | None,
    *,
    context: str | None,
    config: str | None,
    yes: bool = False,
) -> None:
    """Read or update one project config value.

    Parameters
    ----------
    path
        Dot-separated project config path.
    value
        Optional raw CLI value.
    context
        Optional context name.
    config
        Optional direct config path.
    """
    entry = resolve_context(config_path=config, context_name=context)
    if not entry.config:
        raise SFTPWardenError(
            f"Context {entry.name} has no local sftpwarden.yaml.",
            suggestion="Use a local or remote local-sync context, or pass --config.",
        )
    loaded = load_config(entry.config)
    data = loaded.model_dump(mode="json")
    if value is None:
        console.print(format_value(get_dotted(data, path)))
        return
    update_project_config_data(
        entry=entry,
        data=data,
        path=path,
        value=value,
        context_config_path=config,
        yes=yes,
    )


def update_project_config_data(
    *,
    entry: ContextEntry,
    data: dict[str, Any],
    path: str,
    value: str,
    context_config_path: str | None,
    yes: bool,
) -> None:
    """Validate and persist one project config mutation."""
    loaded = SFTPWardenConfig.model_validate(data)
    old_name = loaded.project.name
    set_dotted(data, path, parse_project_config_cli_value(data, path, value))
    updated = SFTPWardenConfig.model_validate(data)
    confirm_provider_schema_config_change(entry, updated, yes=yes, path=path)
    write_config(entry.config, updated)
    if path == "project.name" and updated.project.name != old_name and not context_config_path:
        rename_context_for_project_name(entry.name, updated.project.name)
    print_success(f"Updated [bold]{path}[/bold].")


def confirm_provider_schema_config_change(
    entry: ContextEntry, config: SFTPWardenConfig, *, yes: bool, path: str
) -> None:
    """Ask before accepting a config change that will require provider migration."""
    if path != "provider.user_schema":
        return
    result = plan_provider_schema_reconciliation(entry, config)
    if not result.changed:
        return
    console.print(
        "[yellow]Changing provider.user_schema will require a provider data migration "
        f"from v{result.from_schema} to v{result.to_schema} during the next deploy.[/yellow]"
    )
    if not yes and not typer.confirm("Accept this config change?", default=False):
        raise typer.Exit(1)


def parse_project_config_cli_value(data: dict[str, Any], path: str, value: str) -> Any:
    """Parse a CLI scalar while preserving existing string-typed fields."""
    current = get_dotted(data, path)
    parsed = parse_cli_value(value)
    if isinstance(current, str) and not isinstance(parsed, str):
        return value
    return parsed


def register_project_config_path(path: str) -> None:
    """Register one dynamic project config command.

    Parameters
    ----------
    path
        Dot-separated project config path exposed as a CLI command.
    """

    def command(
        value: Annotated[str | None, typer.Argument(help="Optional new value.")] = None,
        context: Annotated[str | None, typer.Option("--context", "-c")] = None,
        config: Annotated[str | None, typer.Option("--config")] = None,
        yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    ) -> None:
        try:
            update_project_config_value(path, value, context=context, config=config, yes=yes)
        except SFTPWardenError as exc:
            handle_error(exc)
        except ValueError as exc:
            handle_error(cli_error_from_exception(exc))

    command.__name__ = f"config_{path.replace('.', '_')}"
    command.__doc__ = f"Show or update `{path}` in sftpwarden.yaml."
    config_app.command(path)(command)


for _path in PROJECT_CONFIG_PATHS:
    register_project_config_path(_path)


@config_app.command("show")
def config_show(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show global CLI configuration.

    Parameters
    ----------
    json_output
        Whether to emit JSON instead of YAML-like console output.
    """
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
    """Show or update the global default provider.

    Parameters
    ----------
    provider
        Provider value to persist, or ``None`` to print the current default.
    """
    try:
        config = load_global_config()
        if provider is None:
            console.print(config.default_provider.value if config.default_provider else "yaml")
            return
        config.default_provider = ProviderType(provider)
        save_global_config(config)
        print_success(f"Default provider set to [bold]{config.default_provider.value}[/bold].")
    except (SFTPWardenError, ValueError) as exc:
        handle_error(cli_error_from_exception(exc))
