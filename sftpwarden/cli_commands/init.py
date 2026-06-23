from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.prompt import Confirm, Prompt

from sftpwarden.cli_commands.app import app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.prompts import prompt_mongodb_dsn, prompt_sql_dsn
from sftpwarden.config import (
    EXTERNAL_DSN_PROVIDER_TYPES,
    FILE_PROVIDER_TYPES,
    SQL_QUERY_PROVIDER_TYPES,
    ProviderType,
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
    remote_url_from_parts,
    set_default_context,
)
from sftpwarden.providers import (
    empty_provider_text,
    provider_from_config,
)
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.render.compose import write_compose
from sftpwarden.services.cli_workflows import install_context_watcher
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.console import print_info, print_success, terminal_status
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.files import write_private_text
from sftpwarden.utils.paths import expand_path


@app.command()
def init(
    context_name: Annotated[str | None, typer.Argument(help="Context name to create.")] = None,
    context: Annotated[
        str | None, typer.Option("--context", "-c", help="Context name for remote init.")
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Provider type.")] = None,
    root: Annotated[str | None, typer.Option("--root", help="Local project root.")] = None,
    remote: Annotated[
        str | None, typer.Option("--remote", help="Remote URL in user@host:/path form.")
    ] = None,
    remote_url: Annotated[str | None, typer.Option("--remote-url", help="Remote URL.")] = None,
    dsn: Annotated[
        str | None,
        typer.Option(
            "--dsn",
            help=(
                "SQL database URL/DSN, e.g. postgresql://user:pass@db.example.com:5432/sftpwarden."
            ),
        ),
    ] = None,
    query: Annotated[str | None, typer.Option("--query", help="SQL provider read query.")] = None,
    table: Annotated[str, typer.Option("--table", help="SQL users table name.")] = "sftp_users",
    collection: Annotated[
        str, typer.Option("--collection", help="MongoDB users collection name.")
    ] = "sftp_users",
    create_table: Annotated[
        bool | None,
        typer.Option(
            "--create-table/--no-create-table",
            help="Create a missing SQL users table during init.",
        ),
    ] = None,
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
    """Initialize a local or remote SFTPWarden context.

    Parameters
    ----------
    context_name
        Optional positional context name, or ``remote`` for remote init mode.
    context
        Context name for remote init compatibility.
    provider
        Provider type override.
    root
        Local project root.
    remote
        Remote URL shortcut for creating a remote context.
    remote_url
        Optional compact remote URL.
    dsn
        Optional SQL provider DSN.
    query
        Optional SQL provider read query.
    table
        SQL users table name.
    create_table
        Whether to create a missing SQL users table without prompting.
    host
        Remote SSH host used when ``remote_url`` is not provided.
    remote_user
        Optional remote SSH user.
    port
        Optional remote SSH port.
    remote_root
        Remote project root.
    ssh_key
        Optional explicit SSH key path.
    watcher_mode
        Optional watcher mode to install or reuse.
    remote_only
        Whether to create only a remote registry entry.
    skip_checks
        Whether remote prerequisite checks should be skipped.
    critical
        Whether the context should require critical-operation confirmation.
    yes
        Whether confirmation prompts should be skipped.
    """
    try:
        ensure_home()
        if remote is not None and remote_url is not None:
            raise SFTPWardenError("Use either --remote or --remote-url, not both.")
        if context_name == "remote" or remote is not None or remote_url is not None:
            init_remote_context(
                name=context if context_name == "remote" else context_name or context,
                provider=provider,
                root=root,
                remote_url=remote or remote_url,
                dsn=dsn,
                query=query,
                table=table,
                collection=collection,
                create_table=create_table,
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
            print_success(f"Set global default provider to [bold]{selected_provider.value}[/bold].")
        else:
            print_info(f"Using global default provider [bold]{selected_provider.value}[/bold].")
        selected_root = expand_path(root) if root else Path.cwd()
        if (
            root is None
            and not yes
            and not Confirm.ask(f"Use local root {selected_root}?", default=True)
        ):
            selected_root = expand_path(Prompt.ask("Local root", default=str(selected_root)))
        selected_root.mkdir(parents=True, exist_ok=True)
        config = init_project_config(
            name,
            selected_provider,
            dsn=dsn,
            query=query,
            table=table,
            collection=collection,
            yes=yes,
        )
        config_path = selected_root / "sftpwarden.yaml"
        provider_path = provider_local_path(selected_root, config)
        if (
            config_path.exists()
            and not yes
            and not Confirm.ask(f"Overwrite {config_path}?", default=False)
        ):
            raise typer.Exit(1)
        ensure_provider_storage_for_init(
            selected_root,
            config,
            create_storage=create_table,
            yes=yes,
        )
        write_config(config_path, config)
        if config.provider.type == ProviderType.SQLITE and not provider_path.exists():
            provider_from_config(selected_root, config).write(ProviderUsers(users=[]))
        elif config.provider.type in FILE_PROVIDER_TYPES and not provider_path.exists():
            write_private_text(provider_path, empty_provider_text(config.provider.type))
        write_compose(config, selected_root)
        entry = local_context(name, selected_root, selected_provider, critical)
        register_context(entry)
        set_default_context(name)
        print_success(f"Initialized context [bold]{name}[/bold] at {selected_root}.")
    except SFTPWardenError as exc:
        handle_error(exc)


def init_remote_context(
    *,
    name: str | None,
    provider: str | None,
    root: str | None,
    remote_url: str | None,
    dsn: str | None,
    query: str | None,
    table: str,
    collection: str = "sftp_users",
    create_table: bool | None,
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
    """Initialize a remote SFTPWarden context.

    Parameters
    ----------
    name
        Optional context name.
    provider
        Provider type override.
    root
        Optional local root for local-sync contexts.
    remote_url
        Optional compact remote URL.
    dsn
        Optional SQL provider DSN.
    query
        Optional SQL provider read query.
    table
        SQL users table name.
    collection
        MongoDB users collection name.
    create_table
        Whether to create a missing SQL users table without prompting.
    host
        Remote SSH host used when ``remote_url`` is not provided.
    remote_user
        Optional remote SSH user.
    port
        Optional remote SSH port.
    remote_root
        Remote project root override.
    ssh_key
        Optional explicit SSH key path.
    watcher_mode
        Optional watcher mode to install or reuse.
    remote_only
        Whether no local project files should be created.
    skip_checks
        Whether remote prerequisite checks should be skipped.
    critical
        Whether the context should require critical-operation confirmation.
    yes
        Whether confirmation prompts should be skipped.
    """
    context_name = name or Prompt.ask("Context name")
    selected_provider = resolve_provider(provider)
    defaults = load_global_config().defaults
    selected_port = port or defaults.ssh_port
    print_info(f"Using remote SSH port [bold]{selected_port}[/bold].")
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
        selected_root = expand_path(root) if root else Path.cwd()
        if (
            root is None
            and not yes
            and not Confirm.ask(f"Use local root {selected_root}?", default=True)
        ):
            selected_root = expand_path(Prompt.ask("Local root", default=str(selected_root)))
        selected_root.mkdir(parents=True, exist_ok=True)
        config = init_project_config(
            context_name,
            selected_provider,
            dsn=dsn,
            query=query,
            table=table,
            collection=collection,
            yes=yes,
        )
        ensure_provider_storage_for_init(
            selected_root,
            config,
            create_storage=create_table,
            yes=yes,
        )
        write_config(selected_root / "sftpwarden.yaml", config)
        provider_path = provider_local_path(selected_root, config)
        if config.provider.type == ProviderType.SQLITE and not provider_path.exists():
            provider_from_config(selected_root, config).write(ProviderUsers(users=[]))
        elif config.provider.type in FILE_PROVIDER_TYPES and not provider_path.exists():
            write_private_text(provider_path, empty_provider_text(config.provider.type))
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
        with terminal_status(f"Checking remote host {entry.remote.host}"):
            verify_remote_runtime_requirements(entry.remote)
    register_context(entry)
    set_default_context(context_name)
    install_context_watcher(entry, requested_mode=watcher_mode, yes=yes)
    print_success(f"Initialized remote context [bold]{context_name}[/bold].")


def init_project_config(
    name: str,
    provider: ProviderType,
    *,
    dsn: str | None,
    query: str | None,
    table: str,
    collection: str = "sftp_users",
    yes: bool,
):
    """Build a project config for init, prompting for SQL DSNs when needed.

    Parameters
    ----------
    name
        Project/context name.
    provider
        Selected provider type.
    dsn
        Optional SQL provider DSN.
    query
        Optional SQL read query.
    table
        SQL users table name.
    collection
        MongoDB users collection name.
    yes
        Whether prompts should be skipped.

    Returns
    -------
    SFTPWardenConfig
        Project config ready to write.
    """
    resolved_dsn = dsn
    if provider in EXTERNAL_DSN_PROVIDER_TYPES and not resolved_dsn:
        if yes:
            raise SFTPWardenError(
                f"{provider.value} provider requires --dsn.",
                suggestion=(
                    "Pass a database URL with --dsn, or store it in an environment variable."
                ),
            )
        resolved_dsn = (
            prompt_mongodb_dsn() if provider == ProviderType.MONGODB else prompt_sql_dsn(provider)
        )
    return default_project_config(
        name,
        provider,
        dsn=resolved_dsn,
        query=query,
        table=table,
        collection=collection,
    )


def ensure_provider_storage_for_init(
    project_root: Path,
    config,
    *,
    create_storage: bool | None,
    yes: bool,
) -> None:
    """Ensure provider storage exists during init.

    Parameters
    ----------
    project_root
        Project root used to build the provider instance.
    config
        Project config.
    create_storage
        Explicit storage creation decision from CLI flags.
    yes
        Whether prompts should be skipped.
    """
    if config.provider.type not in EXTERNAL_DSN_PROVIDER_TYPES:
        return
    provider = provider_from_config(project_root, config)
    storage_name = (
        config.provider.collection
        if config.provider.type == ProviderType.MONGODB
        else config.provider.table
    )
    with terminal_status(f"Checking provider storage {storage_name}"):
        table_exists = provider.table_exists()  # type: ignore[attr-defined]
    if table_exists:
        return
    should_create = create_storage
    if should_create is None and not yes:
        should_create = Confirm.ask(
            f"Provider storage {storage_name!r} does not exist. Create it now?",
            default=True,
        )
    if should_create is None:
        should_create = True
    if not should_create:
        missing_message = f"Provider storage does not exist: {storage_name}"
        if config.provider.type in SQL_QUERY_PROVIDER_TYPES:
            missing_message = f"SQL users table does not exist: {storage_name}"
        elif config.provider.type == ProviderType.MONGODB:
            missing_message = f"MongoDB collection does not exist: {storage_name}"
        raise SFTPWardenError(
            missing_message,
            suggestion="Create it manually, then run init again.",
        )
    with terminal_status(f"Creating provider storage {storage_name}"):
        provider.create_table()  # type: ignore[attr-defined]
    print_success(f"Created provider storage [bold]{storage_name}[/bold].")


def ensure_sql_table_for_init(
    project_root: Path,
    config,
    *,
    create_table: bool | None,
    yes: bool,
) -> None:
    """Compatibility wrapper for SQL storage checks.

    Parameters
    ----------
    project_root
        Project root used to build the provider instance.
    config
        Project config.
    create_table
        Explicit table creation decision from CLI flags.
    yes
        Whether prompts should be skipped.
    """
    ensure_provider_storage_for_init(
        project_root,
        config,
        create_storage=create_table,
        yes=yes,
    )
