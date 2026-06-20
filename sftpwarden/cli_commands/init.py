from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.prompt import Confirm, Prompt

from sftpwarden.cli_commands.common import (
    app,
    handle_error,
)
from sftpwarden.config import (
    default_project_config,
    provider_local_path,
    write_config,
)
from sftpwarden.config.global_config import (
    ensure_home,
    load_global_config,
    resolve_provider,
    save_global_config,
)
from sftpwarden.contexts import (
    is_production_like,
    local_context,
    parse_remote_url,
    register_context,
    remote_context,
)
from sftpwarden.providers import (
    empty_provider_text,
)
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.render.compose import write_compose
from sftpwarden.services.cli_workflows import install_context_watcher, remote_url_from_parts
from sftpwarden.utils.console import console
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import expand_path


@app.command()
def init(
    context_name: Annotated[str | None, typer.Argument(help="Context name to create.")] = None,
    context: Annotated[
        str | None, typer.Option("--context", "-c", help="Context name for remote init.")
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Provider type.")] = None,
    root: Annotated[str | None, typer.Option("--root", help="Local project root.")] = None,
    remote_url: Annotated[str | None, typer.Option("--remote-url", help="Remote URL.")] = None,
    host: Annotated[str | None, typer.Option("--host", help="Remote host.")] = None,
    remote_user: Annotated[str | None, typer.Option("--user", help="Remote SSH user.")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Remote SSH port.")] = None,
    remote_root: Annotated[str | None, typer.Option("--remote-root", help="Remote root.")] = None,
    ssh_key: Annotated[str | None, typer.Option("--ssh-key", help="Remote SSH key.")] = None,
    watcher_mode: Annotated[str | None, typer.Option("--watcher", help="Watcher mode.")] = None,
    remote_only: Annotated[bool, typer.Option("--remote-only")] = False,
    skip_checks: Annotated[bool, typer.Option("--skip-checks")] = False,
    critical: Annotated[
        bool, typer.Option("--critical", help="Mark this context as critical.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Accept defaults.")] = False,
) -> None:
    try:
        ensure_home()
        if context_name == "remote":
            init_remote_context(
                name=context,
                provider=provider,
                root=root,
                remote_url=remote_url,
                host=host,
                remote_user=remote_user,
                port=port,
                remote_root=remote_root,
                ssh_key=ssh_key,
                watcher_mode=watcher_mode,
                remote_only=remote_only,
                skip_checks=skip_checks,
                critical=critical,
                yes=yes,
            )
            return
        name = context_name or context or Prompt.ask("Context name")
        if (
            is_production_like(name)
            and not critical
            and not yes
            and not Confirm.ask(
                f"Create production-like context '{name}' as non-critical?", default=False
            )
        ):
            raise typer.Exit(1)
        selected_provider = resolve_provider(provider)
        global_config = load_global_config()
        if global_config.default_provider is None:
            global_config.default_provider = selected_provider
            save_global_config(global_config)
            console.print(f"Set global default provider to [bold]{selected_provider.value}[/bold].")
        else:
            console.print(f"Using global default provider [bold]{selected_provider.value}[/bold].")
        selected_root = expand_path(root or global_config.defaults.root)
        if (
            root is None
            and not yes
            and not Confirm.ask(f"Use local root {selected_root}?", default=True)
        ):
            selected_root = expand_path(Prompt.ask("Local root", default=str(selected_root)))
        selected_root.mkdir(parents=True, exist_ok=True)
        config = default_project_config(name, selected_provider)
        config_path = selected_root / "sftpwarden.yaml"
        provider_path = provider_local_path(selected_root, config)
        if (
            config_path.exists()
            and not yes
            and not Confirm.ask(f"Overwrite {config_path}?", default=False)
        ):
            raise typer.Exit(1)
        write_config(config_path, config)
        if not provider_path.exists():
            provider_path.write_text(empty_provider_text(selected_provider), encoding="utf-8")
        write_compose(config, selected_root)
        entry = local_context(name, selected_root, selected_provider, critical)
        register_context(entry)
        console.print(f"[green]Initialized[/green] context [bold]{name}[/bold] at {selected_root}.")
    except SFTPWardenError as exc:
        handle_error(exc)


def init_remote_context(
    *,
    name: str | None,
    provider: str | None,
    root: str | None,
    remote_url: str | None,
    host: str | None,
    remote_user: str | None,
    port: int | None,
    remote_root: str | None,
    ssh_key: str | None,
    watcher_mode: str | None,
    remote_only: bool,
    skip_checks: bool,
    critical: bool,
    yes: bool,
) -> None:
    context_name = name or Prompt.ask("Context name")
    selected_provider = resolve_provider(provider)
    defaults = load_global_config().defaults
    selected_port = port or defaults.ssh_port
    console.print(f"Using remote SSH port [bold]{selected_port}[/bold].")
    if (
        is_production_like(context_name)
        and not critical
        and not yes
        and not Confirm.ask(
            f"Create production-like context '{context_name}' as non-critical?", default=False
        )
    ):
        raise typer.Exit(1)
    remote_url_path = parse_remote_url(remote_url).path if remote_url else None
    selected_remote_root = remote_root or remote_url_path or defaults.remote_root
    if (
        remote_root is None
        and remote_url_path is None
        and not yes
        and not Confirm.ask(f"Use remote root {selected_remote_root}?", default=True)
    ):
        selected_remote_root = Prompt.ask("Remote root", default=selected_remote_root)
    final_remote_url = remote_url or remote_url_from_parts(
        host=host or Prompt.ask("Remote host"),
        remote_root=selected_remote_root,
        remote_user=remote_user,
    )
    selected_root: Path | None = None
    if not remote_only:
        selected_root = expand_path(root or defaults.root)
        if (
            root is None
            and not yes
            and not Confirm.ask(f"Use local root {selected_root}?", default=True)
        ):
            selected_root = expand_path(Prompt.ask("Local root", default=str(selected_root)))
        selected_root.mkdir(parents=True, exist_ok=True)
        config = default_project_config(context_name, selected_provider)
        write_config(selected_root / "sftpwarden.yaml", config)
        provider_path = provider_local_path(selected_root, config)
        if not provider_path.exists():
            provider_path.write_text(empty_provider_text(selected_provider), encoding="utf-8")
        write_compose(config, selected_root)
    entry = remote_context(
        name=context_name,
        provider=selected_provider,
        remote_url=final_remote_url,
        local_root=selected_root,
        remote_root=selected_remote_root,
        remote_only=remote_only,
        ssh_key=ssh_key,
        critical=critical,
        remote_user=remote_user,
        explicit_remote_root=remote_root,
        port=selected_port,
    )
    if entry.remote and not skip_checks:
        verify_remote_runtime_requirements(entry.remote)
    register_context(entry)
    install_context_watcher(entry, requested_mode=watcher_mode, yes=yes)
    console.print(f"[green]Initialized[/green] remote context [bold]{context_name}[/bold].")
