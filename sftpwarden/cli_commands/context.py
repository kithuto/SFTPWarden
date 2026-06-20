from __future__ import annotations

from typing import Annotated

import typer
from rich.prompt import Confirm
from rich.table import Table

from sftpwarden.cli_commands.common import (
    context_app,
    handle_error,
    print_json,
)
from sftpwarden.config import (
    load_config,
)
from sftpwarden.config.global_config import (
    load_global_config,
    resolve_provider,
)
from sftpwarden.contexts import (
    is_production_like,
    load_registry,
    local_context,
    register_context,
    remote_context,
    remove_context,
    resolve_context,
    set_default_context,
)
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.services.cli_workflows import install_context_watcher
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import expand_path


@context_app.command("ls")
def context_ls(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    registry = load_registry()
    if json_output:
        print_json(registry.model_dump_json(indent=2))
        return
    table = Table(title="SFTPWarden contexts")
    table.add_column("Default")
    table.add_column("Name")
    table.add_column("Type")
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


@context_app.command("current")
def context_current() -> None:
    try:
        console.print(resolve_context().name)
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("default")
def context_default(name: str) -> None:
    try:
        set_default_context(name)
        console.print(f"Default context set to [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("use")
def context_use(name: str) -> None:
    context_default(name)


@context_app.command("clear")
def context_clear() -> None:
    registry = load_registry()
    registry.default = None
    from sftpwarden.contexts import save_registry

    save_registry(registry)
    console.print("Default context cleared.")


@context_app.command("show")
def context_show(name: str | None = None) -> None:
    try:
        entry = resolve_context(context_name=name)
        print_json(entry.model_dump_json(indent=2))
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("remove")
def context_remove(name: str, yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    try:
        if not yes and not Confirm.ask(f"Remove context {name} from registry?", default=False):
            raise typer.Exit(1)
        remove_context(name)
        console.print(f"Removed context [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)


@context_app.command("rename")
def context_rename(old_name: str, new_name: str) -> None:
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
        console.print(f"Renamed [bold]{old_name}[/bold] to [bold]{new_name}[/bold].")
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
        console.print(f"Registered context [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)
