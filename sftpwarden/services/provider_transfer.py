from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

from sftpwarden.config import DeployTarget, ProviderType, RemoteStorage, load_config
from sftpwarden.contexts import ContextEntry, ContextType, resolve_context
from sftpwarden.providers import provider_from_config
from sftpwarden.refresh import refresh_context
from sftpwarden.users import ProviderUsers, users_fingerprint
from sftpwarden.users.schemas import detect_csv_schema, users_from_mapping
from sftpwarden.users.schemas import user_schema as user_schema_for
from sftpwarden.users.service import upsert_user
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import write_private_text
from sftpwarden.watcher import editable_sync_target, sync_target

ProviderFormat = Literal["yaml", "csv", "json"]
ImportMode = Literal["merge", "replace"]


@dataclass(frozen=True)
class ProviderMutationResult:
    """Result returned by provider transfer operations."""

    source_count: int
    destination_count: int
    changed: bool
    runtime_changed: bool
    refresh_output: str | None = None
    sync_output: str | None = None
    deploy_required: bool = False
    manual_action: str | None = None


def resolve_provider_context(*, context_name: str | None = None, config_path: str | None = None):
    """Resolve a context, config, and provider.

    Parameters
    ----------
    context_name
        Optional context name.
    config_path
        Optional config path.

    Returns
    -------
    tuple
        Context entry, config, and provider.
    """
    entry = resolve_context(config_path=config_path, context_name=context_name)
    if not entry.root or not entry.config:
        raise ProviderError(
            f"Context {entry.name} has no local provider configuration.",
            suggestion="Use a local or remote local-sync context for provider transfer commands.",
        )
    config = load_config(entry.config)
    return entry, config, provider_from_config(entry.root, config)


def read_context_users(
    *, context_name: str | None = None, config_path: str | None = None
) -> tuple[ContextEntry, ProviderUsers]:
    """Read users from a resolved context.

    Parameters
    ----------
    context_name
        Optional context name.
    config_path
        Optional config path.

    Returns
    -------
    tuple[ContextEntry, ProviderUsers]
        Resolved context and users.
    """
    entry, _config, provider = resolve_provider_context(
        context_name=context_name,
        config_path=config_path,
    )
    return entry, provider.read()


def serialize_users(users: ProviderUsers, fmt: ProviderFormat) -> str:
    """Serialize users in a provider transfer format.

    Parameters
    ----------
    users
        Users to serialize.
    fmt
        Output format.

    Returns
    -------
    str
        Serialized users.
    """
    schema = user_schema_for(users.schema_version)
    data = schema.users_to_mapping(users)
    if fmt == "json":
        return json.dumps(data, indent=2, sort_keys=True) + "\n"
    if fmt == "yaml":
        return yaml.safe_dump(data, sort_keys=False)
    output = io.StringIO()
    fieldnames = schema.csv_fieldnames()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for user in users.users:
        writer.writerow(schema.csv_row_from_user(user))
    return output.getvalue()


def deserialize_users(text: str, fmt: ProviderFormat) -> ProviderUsers:
    """Deserialize users from a provider transfer format.

    Parameters
    ----------
    text
        Input text.
    fmt
        Input format.

    Returns
    -------
    ProviderUsers
        Parsed users.
    """
    if fmt == "json":
        return users_from_mapping(json.loads(text), fallback_schema=1)
    if fmt == "yaml":
        return users_from_mapping(yaml.safe_load(text) or {"users": []}, fallback_schema=1)
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    schema = detect_csv_schema(list(reader.fieldnames or []), fallback_schema=1)
    for row in reader:
        rows.append(schema.user_from_csv_row(row))
    return ProviderUsers(schema_version=schema.version, users=rows)


def infer_format(path: str | Path | None, explicit: str | None = None) -> ProviderFormat:
    """Infer a provider transfer format.

    Parameters
    ----------
    path
        Optional file path.
    explicit
        Optional explicit format.

    Returns
    -------
    ProviderFormat
        Resolved format.
    """
    if explicit:
        if explicit not in {"yaml", "csv", "json"}:
            raise ProviderError("Provider format must be yaml, csv, or json.")
        return cast(ProviderFormat, explicit)
    suffix = Path(path).suffix.lower() if path is not None else ".yaml"
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    return "yaml"


def export_provider_users(
    *,
    context_name: str | None,
    config_path: str | None,
    output: str | None,
    fmt: ProviderFormat,
) -> tuple[ContextEntry, str]:
    """Export provider users.

    Parameters
    ----------
    context_name
        Optional context name.
    config_path
        Optional config path.
    output
        Optional output path.
    fmt
        Output format.

    Returns
    -------
    tuple[ContextEntry, str]
        Context and serialized users.
    """
    entry, users = read_context_users(context_name=context_name, config_path=config_path)
    text = serialize_users(users, fmt)
    if output:
        write_private_text(Path(output), text)
    return entry, text


def import_provider_users(
    *,
    context_name: str | None,
    input_path: str,
    mode: ImportMode,
    fmt: ProviderFormat,
    dry_run: bool = False,
    no_refresh: bool = False,
) -> ProviderMutationResult:
    """Import users into a provider.

    Parameters
    ----------
    context_name
        Destination context name.
    input_path
        Input file path.
    mode
        Merge or replace behavior.
    fmt
        Input format.
    dry_run
        Whether to avoid writing.
    no_refresh
        Whether to skip automatic refresh.

    Returns
    -------
    ProviderMutationResult
        Import result.
    """
    users = deserialize_users(Path(input_path).read_text(encoding="utf-8"), fmt)
    entry, config, provider = resolve_provider_context(context_name=context_name)
    return write_provider_users(
        entry=entry,
        config=config,
        provider=provider,
        source_users=users,
        mode=mode,
        dry_run=dry_run,
        no_refresh=no_refresh,
    )


def copy_provider_users(
    *,
    from_context: str,
    to_context: str,
    mode: ImportMode,
    dry_run: bool = False,
    no_refresh: bool = False,
) -> ProviderMutationResult:
    """Copy users between providers.

    Parameters
    ----------
    from_context
        Source context name.
    to_context
        Destination context name.
    mode
        Merge or replace behavior.
    dry_run
        Whether to avoid writing.
    no_refresh
        Whether to skip automatic refresh.

    Returns
    -------
    ProviderMutationResult
        Copy result.
    """
    _source_entry, source_users = read_context_users(context_name=from_context)
    entry, config, provider = resolve_provider_context(context_name=to_context)
    return write_provider_users(
        entry=entry,
        config=config,
        provider=provider,
        source_users=source_users,
        mode=mode,
        dry_run=dry_run,
        no_refresh=no_refresh,
    )


def write_provider_users(
    *,
    entry: ContextEntry,
    config,
    provider,
    source_users: ProviderUsers,
    mode: ImportMode,
    dry_run: bool,
    no_refresh: bool,
) -> ProviderMutationResult:
    """Write provider users with merge or replace behavior.

    Parameters
    ----------
    entry
        Destination context.
    config
        Destination project config.
    provider
        Destination provider.
    source_users
        Source users.
    mode
        Merge or replace behavior.
    dry_run
        Whether to avoid writing.
    no_refresh
        Whether to skip automatic refresh.

    Returns
    -------
    ProviderMutationResult
        Mutation result.
    """
    current = provider.read()
    next_users = source_users
    if mode == "merge":
        merged = current
        for user in source_users.users:
            merged = upsert_user(merged, user)
        next_users = merged

    changed = current != next_users
    runtime_changed = users_fingerprint(current) != users_fingerprint(next_users)
    sync_output = None
    refresh_output = None
    deploy_required = False
    manual_action = None
    if not dry_run and changed:
        provider.write(next_users)
        sync_output = sync_provider_file_if_needed(entry, config)
        if config.deploy.target == DeployTarget.KUBERNETES and config.provider.type in {
            ProviderType.YAML,
            ProviderType.CSV,
        }:
            deploy_required = True
        elif (
            config.deploy.target == DeployTarget.KUBERNETES
            and config.provider.type == ProviderType.SQLITE
        ):
            manual_action = (
                "SQLite provider changes were saved locally only. SQLite provider files are "
                "not copied into Kubernetes PVCs automatically; use YAML/CSV deploy sync for "
                "declarative file providers or a database provider for production Kubernetes."
            )
        elif runtime_changed and not no_refresh:
            refresh_output = refresh_context(entry)
    return ProviderMutationResult(
        source_count=len(source_users.users),
        destination_count=len(next_users.users),
        changed=changed,
        runtime_changed=runtime_changed,
        refresh_output=refresh_output,
        sync_output=sync_output,
        deploy_required=deploy_required,
        manual_action=manual_action,
    )


def sync_provider_file_if_needed(entry: ContextEntry, config) -> str | None:
    """Sync a local provider file for remote local-sync contexts.

    Parameters
    ----------
    entry
        Destination context.
    config
        Destination project config.

    Returns
    -------
    str | None
        Sync output, or ``None`` when no sync is needed.
    """
    if entry.type != ContextType.REMOTE or entry.storage != RemoteStorage.LOCAL_SYNC:
        return None
    target = editable_sync_target(entry, config)
    if not target:
        return None
    return sync_target(entry, target.local_path, target.remote_path)
