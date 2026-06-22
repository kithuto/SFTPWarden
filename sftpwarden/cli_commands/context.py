from __future__ import annotations

import shutil
from typing import Annotated

import typer
from rich import box
from rich.prompt import Confirm
from rich.table import Table

from sftpwarden.cli_commands.app import context_app
from sftpwarden.cli_commands.output import (
    handle_error,
    print_json,
    print_watcher_without_local_sync_targets,
)
from sftpwarden.cli_commands.prompts import prompt_remote_url
from sftpwarden.config import (
    load_config,
    write_config,
)
from sftpwarden.config.global_config import (
    load_global_config,
    resolve_provider,
)
from sftpwarden.contexts import (
    ContextRegistry,
    ContextType,
    is_production_like,
    load_registry,
    local_context,
    register_context,
    remote_context,
    remove_context,
    require_initialized_context,
    resolve_context,
    save_registry,
    set_default_context,
)
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.services.cli_workflows import install_context_watcher
from sftpwarden.utils.console import console, print_success
from sftpwarden.utils.constants import CONFIG_FILENAME
from sftpwarden.utils.dotted import format_value, get_dotted, parse_cli_value, set_dotted
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import expand_path


@context_app.callback(invoke_without_command=True)
def context_value(
    ctx: typer.Context,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    remote_url: Annotated[str | None, typer.Option("--remote")] = None,
    root: Annotated[str | None, typer.Option("--root")] = None,
    remote_user: Annotated[str | None, typer.Option("--user")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    remote_root: Annotated[str | None, typer.Option("--remote-root")] = None,
    remote_only: Annotated[bool, typer.Option("--remote-only")] = False,
    delete_old_root: Annotated[bool, typer.Option("--delete-old-root")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Read or update one field in the active context.

    Parameters
    ----------
    ctx
        Typer context.
    context
        Optional context name to edit.
    remote_url
        Optional remote URL used when converting a context to remote.
    root
        Optional local root used by root migrations or type conversion.
    remote_user
        Optional remote SSH user.
    port
        Optional remote SSH port.
    remote_root
        Optional remote root.
    remote_only
        Whether a new remote context should be remote-only.
    delete_old_root
        Whether to delete the old local root after copying it.
    yes
        Whether confirmation prompts should be skipped.
    """
    if ctx.invoked_subcommand is not None:
        return
    args = list(ctx.args)
    if not args:
        return
    if len(args) > 2:
        handle_error(SFTPWardenError("Usage: sftpwarden context <field> [value]"))
    field = args[0]
    value = args[1] if len(args) == 2 else None
    try:
        entry = resolve_context(context_name=context)
        data = entry.model_dump(mode="json", exclude_none=True)
        normalized_field = normalize_context_field(field)
        if value is None:
            console.print(format_value(get_dotted(data, normalized_field)))
            raise typer.Exit()
        updated_name = update_context_field(
            entry.name,
            normalized_field,
            value,
            remote_url=remote_url,
            root=root,
            remote_user=remote_user,
            port=port,
            remote_root=remote_root,
            remote_only=remote_only,
            delete_old_root=delete_old_root,
            yes=yes,
        )
        print_success(f"Updated context [bold]{updated_name}[/bold] field [bold]{field}[/bold].")
        raise typer.Exit()
    except SFTPWardenError as exc:
        handle_error(exc)
    except ValueError as exc:
        handle_error(SFTPWardenError(str(exc)))


def normalize_context_field(field: str) -> str:
    """Normalize context field aliases.

    Parameters
    ----------
    field
        Field supplied by the CLI.

    Returns
    -------
    str
        Canonical field path.
    """
    aliases = {
        "remote_root": "remote.remote_root",
        "remote_config": "remote.remote_config",
        "ssh_key": "remote.ssh_key",
        "host": "remote.host",
        "user": "remote.user",
        "port": "remote.port",
    }
    return aliases.get(field, field)


def update_context_field(
    context_name: str,
    field: str,
    value: str,
    *,
    remote_url: str | None,
    root: str | None,
    remote_user: str | None,
    port: int | None,
    remote_root: str | None,
    remote_only: bool,
    delete_old_root: bool,
    yes: bool,
) -> str:
    """Update a context field and persist the registry.

    Parameters
    ----------
    context_name
        Context name to update.
    field
        Canonical field path.
    value
        Raw CLI value.
    remote_url
        Optional remote URL.
    root
        Optional local root.
    remote_user
        Optional remote SSH user.
    port
        Optional remote SSH port.
    remote_root
        Optional remote root.
    remote_only
        Whether new remote context should be remote-only.
    delete_old_root
        Whether old local root should be deleted after migration.
    yes
        Whether confirmation prompts should be skipped.

    Returns
    -------
    str
        Final context name.
    """
    registry = load_registry()
    if context_name not in registry.contexts:
        raise SFTPWardenError(f"Unknown context: {context_name}")
    entry = registry.contexts[context_name]
    if field == "name":
        return rename_context_and_project(registry, context_name, value)
    if field == "root":
        entry = migrate_context_root(entry, value, delete_old_root=delete_old_root, yes=yes)
    elif field == "type":
        entry = convert_context_type(
            entry,
            value,
            remote_url=remote_url,
            root=root,
            remote_user=remote_user,
            port=port,
            remote_root=remote_root,
            remote_only=remote_only,
            yes=yes,
        )
    elif field == "remote.remote_root":
        entry = update_remote_root(entry, value, yes=yes)
    else:
        data = entry.model_dump(mode="json", exclude_none=True)
        set_dotted(data, field, parse_cli_value(value))
        entry = type(entry).model_validate(data)
    registry.contexts[entry.name] = entry
    save_registry(registry)
    warn_if_watcher_has_no_local_sync_targets(registry)
    return entry.name


def rename_context_and_project(registry, old_name: str, new_name: str) -> str:
    """Rename a context and its local project config when present."""
    if new_name in registry.contexts and new_name != old_name:
        raise SFTPWardenError(f"Context already exists: {new_name}")
    entry = registry.contexts.pop(old_name)
    entry.name = new_name
    if entry.config:
        config_path = expand_path(entry.config)
        if config_path.exists():
            config = load_config(config_path)
            config.project.name = new_name
            write_config(config_path, config)
    registry.contexts[new_name] = entry
    if registry.default == old_name:
        registry.default = new_name
    save_registry(registry)
    return new_name


def migrate_context_root(entry, new_root_value: str, *, delete_old_root: bool, yes: bool):
    """Copy a context project to a new local root and update paths."""
    old_root = expand_path(entry.root)
    new_root = expand_path(new_root_value)
    if old_root == new_root:
        return entry
    if not yes and not Confirm.ask(
        f"Copy context files from {old_root} to {new_root}?", default=False
    ):
        raise typer.Exit(1)
    if old_root.exists():
        shutil.copytree(old_root, new_root, dirs_exist_ok=True)
    else:
        new_root.mkdir(parents=True, exist_ok=True)
    if (
        delete_old_root
        and old_root.exists()
        and (yes or Confirm.ask(f"Delete old root {old_root}?", default=False))
    ):
        shutil.rmtree(old_root)
    entry.root = str(new_root)
    entry.config = str(new_root / CONFIG_FILENAME)
    return entry


def convert_context_type(
    entry,
    value: str,
    *,
    remote_url: str | None,
    root: str | None,
    remote_user: str | None,
    port: int | None,
    remote_root: str | None,
    remote_only: bool,
    yes: bool,
):
    """Convert a context between local and remote."""
    target = value.lower()
    if target == entry.type.value:
        return entry
    if target == "local":
        if not yes and not Confirm.ask(
            f"Convert remote context {entry.name} to local and remove remote metadata?",
            default=False,
        ):
            raise typer.Exit(1)
        local_root = expand_path(root or entry.root or f"~/sftpwarden-{entry.name}")
        entry.type = ContextType.LOCAL
        entry.root = str(local_root)
        entry.config = str(local_root / CONFIG_FILENAME)
        entry.storage = None
        entry.watcher_required = False
        entry.remote = None
        return entry
    if target != "remote":
        raise SFTPWardenError("Context type must be local or remote.")
    final_remote_url = remote_url
    if not final_remote_url:
        final_remote_url = prompt_remote_url(
            remote_user=remote_user,
            remote_root=remote_root,
        )
    provider = entry.provider
    return remote_context(
        name=entry.name,
        provider=provider,
        remote_url=final_remote_url,
        local_root=root or entry.root or None,
        remote_root=remote_root or "~/sftpwarden",
        remote_only=remote_only,
        ssh_key=entry.remote.ssh_key if entry.remote else None,
        critical=entry.critical,
        remote_user=remote_user,
        explicit_remote_root=remote_root,
        port=port or (entry.remote.port if entry.remote else 22),
    )


def update_remote_root(entry, value: str, *, yes: bool):
    """Update remote root and dependent remote config path."""
    if not entry.remote:
        raise SFTPWardenError("Context has no remote settings.")
    prompt = (
        f"Update remote root for {entry.name} to {value}? Remote files are not moved automatically."
    )
    if not yes and not Confirm.ask(prompt, default=False):
        raise typer.Exit(1)
    entry.remote.remote_root = value
    entry.remote.remote_config = f"{value.rstrip('/')}/{CONFIG_FILENAME}"
    return entry


def warn_if_watcher_has_no_local_sync_targets(registry: ContextRegistry) -> None:
    """Warn when an installed watcher no longer has local-sync contexts.

    Parameters
    ----------
    registry
        Context registry after a mutation.
    """
    watcher = load_global_config().watcher
    if not watcher.installed:
        return
    has_local_sync = any(
        entry.type == ContextType.REMOTE and entry.storage == "local-sync"
        for entry in registry.contexts.values()
    )
    if not has_local_sync:
        print_watcher_without_local_sync_targets()


CONTEXT_FIELD_COMMANDS = {
    "name": "name",
    "type": "type",
    "root": "root",
    "config": "config",
    "provider": "provider",
    "critical": "critical",
    "storage": "storage",
    "watcher-required": "watcher_required",
    "watcher_required": "watcher_required",
    "remote-root": "remote.remote_root",
    "remote_root": "remote.remote_root",
    "remote.remote_root": "remote.remote_root",
    "remote-config": "remote.remote_config",
    "remote_config": "remote.remote_config",
    "remote.remote_config": "remote.remote_config",
    "ssh-key": "remote.ssh_key",
    "ssh_key": "remote.ssh_key",
    "remote.ssh_key": "remote.ssh_key",
    "host": "remote.host",
    "remote.host": "remote.host",
    "user": "remote.user",
    "remote.user": "remote.user",
    "port": "remote.port",
    "remote.port": "remote.port",
    "compose-file": "remote.compose_file",
    "compose_file": "remote.compose_file",
    "remote.compose_file": "remote.compose_file",
}


def register_context_field_command(command_name: str, field: str) -> None:
    """Register one context field editor command.

    Parameters
    ----------
    command_name
        CLI command name.
    field
        Canonical context field path.
    """

    def command(
        value: Annotated[str | None, typer.Argument(help="Optional new value.")] = None,
        context: Annotated[str | None, typer.Option("--context", "-c")] = None,
        remote_url: Annotated[str | None, typer.Option("--remote")] = None,
        root: Annotated[str | None, typer.Option("--root")] = None,
        remote_user: Annotated[str | None, typer.Option("--user")] = None,
        port: Annotated[int | None, typer.Option("--port")] = None,
        remote_root: Annotated[str | None, typer.Option("--remote-root")] = None,
        remote_only: Annotated[bool, typer.Option("--remote-only")] = False,
        delete_old_root: Annotated[bool, typer.Option("--delete-old-root")] = False,
        yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    ) -> None:
        try:
            entry = resolve_context(context_name=context)
            data = entry.model_dump(mode="json", exclude_none=True)
            if value is None:
                console.print(format_value(get_dotted(data, field)))
                return
            updated_name = update_context_field(
                entry.name,
                field,
                value,
                remote_url=remote_url,
                root=root,
                remote_user=remote_user,
                port=port,
                remote_root=remote_root,
                remote_only=remote_only,
                delete_old_root=delete_old_root,
                yes=yes,
            )
            print_success(
                f"Updated context [bold]{updated_name}[/bold] field [bold]{command_name}[/bold]."
            )
        except SFTPWardenError as exc:
            handle_error(exc)
        except ValueError as exc:
            handle_error(SFTPWardenError(str(exc)))

    command.__name__ = f"context_{command_name.replace('.', '_').replace('-', '_')}"
    command.__doc__ = f"Show or update context field `{field}`."
    context_app.command(command_name)(command)


for _command_name, _field in CONTEXT_FIELD_COMMANDS.items():
    register_context_field_command(_command_name, _field)


@context_app.command("ls")
def context_ls(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List registered contexts.

    Parameters
    ----------
    json_output
        Whether to emit raw registry JSON.
    """
    try:
        require_initialized_context()
        registry = load_registry()
        if json_output:
            print_json(registry.model_dump_json(indent=2))
            return
        table = Table(title="SFTPWarden contexts", box=box.SIMPLE_HEAVY, header_style="bold cyan")
        table.add_column("Default", justify="center")
        table.add_column("Name", style="bold")
        table.add_column("Type", style="cyan")
        table.add_column("Provider")
        table.add_column("Root")
        for name, entry in registry.contexts.items():
            table.add_row(
                "*" if registry.default == name else "",
                name,
                entry.type.value,
                entry.provider.value,
                entry.root,
            )
        console.print(table)
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("current")
def context_current() -> None:
    """Print the currently selected context name."""
    try:
        console.print(resolve_context().name)
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("default")
def context_default(name: str) -> None:
    """Set the default context.

    Parameters
    ----------
    name
        Context name to mark as default.
    """
    try:
        set_default_context(name)
        print_success(f"Default context set to [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("use")
def context_use(name: str) -> None:
    """Alias for setting the default context.

    Parameters
    ----------
    name
        Context name to mark as default.
    """
    context_default(name)


@context_app.command("clear")
def context_clear() -> None:
    """Clear the default context from the registry."""
    try:
        require_initialized_context()
        registry = load_registry()
        registry.default = None
        from sftpwarden.contexts import save_registry

        save_registry(registry)
        print_success("Default context cleared.")
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("show")
def context_show(name: str | None = None) -> None:
    """Show a context as JSON.

    Parameters
    ----------
    name
        Optional context name; defaults to the active context.
    """
    try:
        entry = resolve_context(context_name=name)
        print_json(entry.model_dump_json(indent=2))
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("remove")
def context_remove(name: str, yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """Remove a context from the registry.

    Parameters
    ----------
    name
        Context name to remove.
    yes
        Whether to skip the confirmation prompt.
    """
    try:
        if not yes and not Confirm.ask(f"Remove context {name} from registry?", default=False):
            raise typer.Exit(1)
        remove_context(name)
        print_success(f"Removed context [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("rename")
def context_rename(old_name: str, new_name: str) -> None:
    """Rename a registered context.

    Parameters
    ----------
    old_name
        Existing context name.
    new_name
        Replacement context name.
    """
    try:
        registry = load_registry()
        if old_name not in registry.contexts:
            raise SFTPWardenError(
                f"Unknown context: {old_name}", suggestion="Run `sftpwarden context ls`."
            )
        entry = registry.contexts.pop(old_name)
        entry.name = new_name
        registry.contexts[new_name] = entry
        if registry.default == old_name:
            registry.default = new_name
        from sftpwarden.contexts import save_registry

        save_registry(registry)
        print_success(f"Renamed [bold]{old_name}[/bold] to [bold]{new_name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("add")
def context_add(
    name: str,
    remote_url: Annotated[
        str | None, typer.Argument(help="Optional user@host:/path for remote contexts.")
    ] = None,
    root: Annotated[str | None, typer.Option("--root")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    remote_user: Annotated[str | None, typer.Option("--user")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    remote_root: Annotated[str | None, typer.Option("--remote-root")] = None,
    remote_only: Annotated[bool, typer.Option("--remote-only")] = False,
    ssh_key: Annotated[str | None, typer.Option("--ssh-key")] = None,
    watcher_mode: Annotated[str | None, typer.Option("--watcher", help="Watcher mode.")] = None,
    critical: Annotated[bool, typer.Option("--critical")] = False,
    skip_checks: Annotated[bool, typer.Option("--skip-checks")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Register a local or remote context.

    Parameters
    ----------
    name
        Context name to register.
    remote_url
        Optional compact remote URL in ``user@host:/path`` form.
    root
        Local project root for local or local-sync contexts.
    provider
        Provider type override.
    remote_user
        Optional SSH user for remote contexts.
    port
        Optional SSH port.
    remote_root
        Remote project root override.
    remote_only
        Whether the context exists only on the remote host.
    ssh_key
        Optional explicit SSH key path.
    watcher_mode
        Optional watcher mode to install or reuse.
    critical
        Whether the context should require critical-operation confirmation.
    skip_checks
        Whether remote prerequisite checks should be skipped.
    yes
        Whether confirmation prompts should be skipped.
    """
    try:
        selected_provider = resolve_provider(provider)
        if (
            is_production_like(name)
            and not critical
            and not yes
            and not Confirm.ask(
                f"Create production-like context '{name}' as non-critical?", default=False
            )
        ):
            raise typer.Exit(1)
        if remote_url:
            defaults = load_global_config().defaults
            entry = remote_context(
                name=name,
                provider=selected_provider,
                remote_url=remote_url,
                local_root=root,
                remote_root=defaults.remote_root,
                remote_only=remote_only,
                ssh_key=ssh_key,
                critical=critical,
                remote_user=remote_user,
                explicit_remote_root=remote_root,
                port=port or defaults.ssh_port,
            )
            if entry.remote and not skip_checks:
                verify_remote_runtime_requirements(entry.remote)
        else:
            selected_root = expand_path(root or ".")
            loaded = load_config(selected_root / "sftpwarden.yaml")
            entry = local_context(name, selected_root, loaded.provider.type, critical)
        register_context(entry)
        install_context_watcher(entry, requested_mode=watcher_mode, yes=yes)
        print_success(f"Registered context [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)
