from __future__ import annotations

import hmac
import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.prompt import Confirm, Prompt
from rich.table import Table

from sftpwarden import __version__
from sftpwarden.config import (
    ProviderType,
    default_project_config,
    load_config,
    provider_local_path,
    write_config,
)
from sftpwarden.config.global_config import (
    ensure_home,
    global_config_data,
    load_global_config,
    resolve_provider,
    save_global_config,
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
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.providers import (
    SFTPUser,
    empty_provider_text,
    find_user,
    load_users_from_project,
    provider_from_config,
)
from sftpwarden.refresh import refresh_context, resolve_refresh_targets
from sftpwarden.remote.checks import verify_remote_runtime_requirements
from sftpwarden.render.compose import compose_text, write_compose
from sftpwarden.runtime import (
    RuntimePlan,
    RuntimeState,
    apply_once,
    build_runtime_plan,
    load_runtime_inputs,
    run_sync_loop,
)
from sftpwarden.security.passwords import resolve_password_hash
from sftpwarden.utils.console import console
from sftpwarden.utils.paths import expand_path
from sftpwarden.watcher import derive_watch_targets, poll_watch

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


@app.command()
def init(
    context_name: Annotated[str | None, typer.Argument(help="Context name to create.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Provider type.")] = None,
    root: Annotated[str | None, typer.Option("--root", help="Local project root.")] = None,
    critical: Annotated[
        bool, typer.Option("--critical", help="Mark this context as critical.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Accept defaults.")] = False,
) -> None:
    try:
        ensure_home()
        name = context_name or Prompt.ask("Context name")
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


@app.command()
def info(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        entry = resolve_context(config_path=config, context_name=context)
        if json_output:
            console.print(entry.model_dump_json(indent=2))
            return
        table = Table(title=f"Context {entry.name}")
        table.add_column("Field")
        table.add_column("Value")
        for key, value in entry.model_dump(mode="json", exclude_none=True).items():
            table.add_row(key, json.dumps(value) if isinstance(value, dict) else str(value))
        console.print(table)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def validate(
    config: Annotated[str, typer.Option("--config", help="Config file path.")] = "sftpwarden.yaml",
) -> None:
    try:
        loaded = load_config(config)
        provider_path = provider_local_path(Path(config).parent, loaded)
        console.print(
            f"[green]Valid config[/green] for project [bold]{loaded.project.name}[/bold]."
        )
        console.print(f"Provider: {loaded.provider.type.value} at {provider_path}")
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def compose(
    config: Annotated[str, typer.Option("--config")] = "sftpwarden.yaml",
    write: Annotated[bool, typer.Option("--write", help="Write docker-compose.yml.")] = False,
) -> None:
    try:
        path = expand_path(config)
        loaded = load_config(path)
        if write:
            target = write_compose(loaded, path.parent)
            console.print(f"[green]Wrote[/green] {target}")
            return
        console.print(compose_text(loaded, path.parent))
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def plan(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        entry = resolve_context(config_path=config, context_name=context)
        if not entry.root or not entry.config:
            raise SFTPWardenError(
                f"Context {entry.name} has no local config to plan.",
                suggestion="Use `sftpwarden refresh --dry-run` for remote-only contexts.",
            )
        loaded = load_config(entry.config)
        users = load_users_from_project(entry.root, loaded)
        state = RuntimeState.load(Path(entry.root) / "state" / "state.json")
        runtime_plan = build_runtime_plan(loaded, users, state)
        if json_output:
            console.print(runtime_plan_to_json(runtime_plan))
            return
        console.print(f"Context: [bold]{entry.name}[/bold]")
        console.print(f"Provider users: {len(users.users)}")
        console.print(runtime_plan.summary())
        print_runtime_plan(runtime_plan)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def refresh(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    all_contexts: Annotated[bool, typer.Option("--all")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    try:
        targets = resolve_refresh_targets(
            all_contexts=all_contexts, context_name=context, config_path=config
        )
        for target in targets:
            console.print(refresh_context(target, dry_run=dry_run))
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def sync(dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    try:
        targets = derive_watch_targets()
        for target in targets:
            console.print(f"{target.context}: {target.local_path} -> {target.remote_path}")
        if dry_run:
            console.print("Dry run only; no files synced.")
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def watch(
    interval: Annotated[int, typer.Option("--interval", min=1)] = 2,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    try:
        console.print("Watching remote local-sync contexts.")
        poll_watch(interval_seconds=interval, dry_run=dry_run)
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def deploy(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    try:
        entry = resolve_context(context_name=context)
        command = f"cd {entry.root} && docker compose up -d --build"
        if dry_run:
            console.print(command)
            return
        raise SFTPWardenError(
            "Automated deploy is intentionally not enabled yet.",
            suggestion=f"Review generated files, then run `{command}`.",
        )
    except SFTPWardenError as exc:
        handle_error(exc)


@app.command()
def doctor() -> None:
    console.print("SFTPWarden doctor")
    for binary in ("docker", "ssh", "rsync"):
        status = "available" if shutil.which(binary) else "check PATH"
        console.print(f"- {binary}: {status}")


@config_app.command("show")
def config_show(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        data = global_config_data()
        console.print(
            json.dumps(data, indent=2) if json_output else yaml.safe_dump(data, sort_keys=False)
        )
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


@context_app.command("ls")
def context_ls(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    registry = load_registry()
    if json_output:
        console.print(registry.model_dump_json(indent=2))
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
        console.print(entry.model_dump_json(indent=2))
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
        console.print(f"Registered context [bold]{name}[/bold].")
    except SFTPWardenError as exc:
        handle_error(exc)


@watcher_app.command("status")
def watcher_status() -> None:
    targets = derive_watch_targets()
    console.print(f"Remote local-sync targets: {len(targets)}")
    for target in targets:
        console.print(f"- {target.context}: {target.local_path}")


@watcher_app.command("install")
def watcher_install() -> None:
    raise typer.BadParameter(
        "Watcher installation is scaffolded; use `sftpwarden watch` in a user service for now."
    )


@watcher_app.command("uninstall")
def watcher_uninstall() -> None:
    raise typer.BadParameter(
        "Watcher uninstall is scaffolded; remove the user service you installed."
    )


@app.command("users")
def users_list(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        entry = resolve_context(config_path=config, context_name=context)
        loaded = load_config(entry.config)
        users = load_users_from_project(entry.root, loaded)
        if json_output:
            console.print(users.model_dump_json(indent=2))
            return
        table = Table(title=f"Users in {entry.name}")
        table.add_column("Username")
        table.add_column("Keys")
        table.add_column("UID")
        table.add_column("GID")
        table.add_column("Disabled")
        for user in users.users:
            table.add_row(
                user.username,
                str(len(user.public_keys)),
                str(user.uid or ""),
                str(user.gid or ""),
                str(user.disabled),
            )
        console.print(table)
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("show")
def user_show(
    username: str, context: Annotated[str | None, typer.Option("--context", "-c")] = None
) -> None:
    try:
        entry = resolve_context(context_name=context)
        loaded = load_config(entry.config)
        user = find_user(load_users_from_project(entry.root, loaded), username)
        console.print(user.model_dump_json(indent=2))
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
    uid: Annotated[int | None, typer.Option("--uid")] = None,
    gid: Annotated[int | None, typer.Option("--gid")] = None,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    try:
        entry = resolve_context(context_name=context)
        loaded = load_config(entry.config)
        provider = provider_from_config(entry.root, loaded)
        resolved_password_hash = prompt_password_hash(
            password=password,
            password_hash=password_hash,
            prompt_if_missing=loaded.auth.allow_password,
        )
        user = SFTPUser(
            username=username,
            public_keys=public_key or [],
            password_hash=resolved_password_hash,
            upload_dir=upload_dir,
            uid=uid,
            gid=gid,
        )
        provider.upsert_user(user)
        console.print(f"[green]Saved[/green] user [bold]{username}[/bold].")
        if not no_refresh:
            console.print(refresh_context(entry, dry_run=True))
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
    disabled: Annotated[bool | None, typer.Option("--disabled/--enabled")] = None,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
) -> None:
    try:
        entry = resolve_context(context_name=context)
        loaded = load_config(entry.config)
        provider = provider_from_config(entry.root, loaded)
        users = provider.read()
        existing = find_user(users, username)
        resolved_password_hash = prompt_password_hash(
            password=password,
            password_hash=password_hash,
        )
        updated = existing.model_copy(
            update={
                "public_keys": public_key if public_key is not None else existing.public_keys,
                "password_hash": resolved_password_hash
                if resolved_password_hash is not None
                else existing.password_hash,
                "disabled": disabled if disabled is not None else existing.disabled,
            }
        )
        provider.upsert_user(updated)
        console.print(f"[green]Updated[/green] user [bold]{username}[/bold].")
        console.print(refresh_context(entry, dry_run=True))
    except SFTPWardenError as exc:
        handle_error(exc)


@user_app.command("remove")
def user_remove(
    username: str,
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    try:
        if not yes and not Confirm.ask(
            f"Remove user {username}? User data will not be deleted.", default=False
        ):
            raise typer.Exit(1)
        entry = resolve_context(context_name=context)
        loaded = load_config(entry.config)
        provider_from_config(entry.root, loaded).remove_user(username)
        console.print(f"[green]Removed[/green] user [bold]{username}[/bold].")
        console.print(refresh_context(entry, dry_run=True))
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("refresh")
def runtime_refresh(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
) -> None:
    try:
        console.print(apply_once(config, force=True))
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("plan")
def runtime_plan(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        loaded, users, state = load_runtime_inputs(config)
        sync_plan = build_runtime_plan(loaded, users, state)
        if json_output:
            console.print(runtime_plan_to_json(sync_plan))
            return
        console.print(sync_plan.summary())
        print_runtime_plan(sync_plan)
    except SFTPWardenError as exc:
        handle_error(exc)


@runtime_app.command("sync")
def runtime_sync(
    config: Annotated[str, typer.Option("--config")] = "/etc/sftpwarden/sftpwarden.yaml",
) -> None:
    try:
        run_sync_loop(config)
    except SFTPWardenError as exc:
        handle_error(exc)
