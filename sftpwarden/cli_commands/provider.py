from __future__ import annotations

from typing import Annotated

import typer

from sftpwarden.cli_commands.app import provider_app
from sftpwarden.cli_commands.errors import handle_error
from sftpwarden.cli_commands.output import print_json
from sftpwarden.services.provider_transfer import (
    ProviderMutationResult,
    copy_provider_users,
    export_provider_users,
    import_provider_users,
    infer_format,
)
from sftpwarden.utils.console import console, print_success
from sftpwarden.utils.errors import ProviderError, SFTPWardenError


@provider_app.command("export")
def provider_export(
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    output: Annotated[str | None, typer.Option("--output", "-o")] = None,
    format_name: Annotated[str | None, typer.Option("--format")] = None,
) -> None:
    """Export users from a provider.

    Parameters
    ----------
    context
        Optional context name.
    config
        Optional config path.
    output
        Optional output file.
    format_name
        Optional output format.
    """
    try:
        fmt = infer_format(output, format_name)
        _entry, text = export_provider_users(
            context_name=context,
            config_path=config,
            output=output,
            fmt=fmt,
        )
        if output:
            print_success(f"Exported provider users to [bold]{output}[/bold].")
            return
        console.file.write(text)
        console.file.flush()
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_app.command("import")
def provider_import(
    input_path: Annotated[str, typer.Option("--input", "-i")],
    context: Annotated[str | None, typer.Option("--context", "-c")] = None,
    format_name: Annotated[str | None, typer.Option("--format")] = None,
    merge: Annotated[bool, typer.Option("--merge")] = False,
    replace: Annotated[bool, typer.Option("--replace")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Import users into a provider.

    Parameters
    ----------
    input_path
        Input provider transfer file.
    context
        Destination context.
    format_name
        Optional input format.
    merge
        Whether to merge users into the destination provider.
    replace
        Whether to replace the destination provider users.
    dry_run
        Whether to avoid writing.
    json_output
        Whether to emit JSON.
    no_refresh
        Whether to skip runtime refresh.
    """
    try:
        result = import_provider_users(
            context_name=context,
            input_path=input_path,
            mode=resolve_transfer_mode(merge=merge, replace=replace),
            fmt=infer_format(input_path, format_name),
            dry_run=dry_run,
            no_refresh=no_refresh,
        )
        print_provider_mutation_result(result, dry_run=dry_run, json_output=json_output)
    except SFTPWardenError as exc:
        handle_error(exc)


@provider_app.command("copy")
def provider_copy(
    from_context: Annotated[str, typer.Option("--from-context")],
    to_context: Annotated[str, typer.Option("--to-context")],
    merge: Annotated[bool, typer.Option("--merge")] = False,
    replace: Annotated[bool, typer.Option("--replace")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
) -> None:
    """Copy users between providers.

    Parameters
    ----------
    from_context
        Source context.
    to_context
        Destination context.
    merge
        Whether to merge users into the destination provider.
    replace
        Whether to replace the destination provider users.
    dry_run
        Whether to avoid writing.
    json_output
        Whether to emit JSON.
    no_refresh
        Whether to skip runtime refresh.
    """
    try:
        result = copy_provider_users(
            from_context=from_context,
            to_context=to_context,
            mode=resolve_transfer_mode(merge=merge, replace=replace),
            dry_run=dry_run,
            no_refresh=no_refresh,
        )
        print_provider_mutation_result(result, dry_run=dry_run, json_output=json_output)
    except SFTPWardenError as exc:
        handle_error(exc)


def resolve_transfer_mode(*, merge: bool, replace: bool):
    """Resolve transfer mode flags.

    Parameters
    ----------
    merge
        Whether merge mode was requested.
    replace
        Whether replace mode was requested.

    Returns
    -------
    str
        Transfer mode.
    """
    if merge == replace:
        raise ProviderError("Use exactly one of --merge or --replace.")
    return "merge" if merge else "replace"


def print_provider_mutation_result(
    result: ProviderMutationResult, *, dry_run: bool, json_output: bool
) -> None:
    """Print provider mutation output.

    Parameters
    ----------
    result
        Mutation result.
    dry_run
        Whether the operation was a dry-run.
    json_output
        Whether to emit JSON.
    """
    data = {
        "dry_run": dry_run,
        "source_count": result.source_count,
        "destination_count": result.destination_count,
        "changed": result.changed,
        "runtime_changed": result.runtime_changed,
        "synced": result.sync_output is not None,
        "refreshed": result.refresh_output is not None,
    }
    if json_output:
        print_json(data)
        return
    action = "Would update" if dry_run and result.changed else "Updated"
    if not result.changed:
        action = "No provider changes detected"
    print_success(
        f"{action}. Source users: [bold]{result.source_count}[/bold], "
        f"destination users: [bold]{result.destination_count}[/bold]."
    )
    if result.sync_output:
        console.print(result.sync_output)
    if result.refresh_output:
        console.print(result.refresh_output)
