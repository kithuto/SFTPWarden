"""Recoverable publication of provider data and its matching configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from sftpwarden.config import ProviderConfig, SFTPWardenConfig, load_config, write_config
from sftpwarden.contexts import ContextEntry
from sftpwarden.providers import BaseProvider, provider_from_config
from sftpwarden.providers.schema import read_schema_snapshot
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import write_private_text
from sftpwarden.utils.persistence import reject_symlink, sync_directory
from sftpwarden.utils.state_transactions import state_transaction


class SchemaTransition(BaseModel):
    """Private recovery record, published before any provider mutation."""

    model_config = ConfigDict(extra="forbid")
    provider: ProviderConfig
    source: ProviderUsers
    target: ProviderUsers
    backup_path: str | None = None


def transition_path(entry: ContextEntry) -> Path:
    """Keep journals distinct when a directory contains several project configs."""
    if not entry.config or not entry.root:
        raise ProviderError("Schema migration requires a local project configuration.")
    path = Path(entry.config)
    return path.parent / ".sftpwarden" / f"{path.name}.schema-transition.json"


def pending_transition(entry: ContextEntry) -> SchemaTransition | None:
    """Inspect recovery state without creating or changing anything."""
    path = transition_path(entry)
    reject_symlink(path)
    if not path.exists():
        return None
    try:
        return SchemaTransition.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise ProviderError(
            "Provider schema recovery record is unreadable or invalid.",
            suggestion="Preserve the recovery record and inspect it before changing provider data.",
        ) from None


def require_no_pending_transition(entry: ContextEntry) -> None:
    """Prevent ordinary edits from bypassing an interrupted schema migration."""
    if pending_transition(entry) is not None:
        raise ProviderError(
            "A provider schema migration is incomplete.",
            suggestion="Retry `sftpwarden provider schema migrate --to 2` before editing users.",
        )


def check_transition_config(record: SchemaTransition, config: SFTPWardenConfig) -> None:
    """Reject provider retargeting and downgrades without exposing its settings."""
    if record.provider.model_dump(exclude={"user_schema"}) != config.provider.model_dump(
        exclude={"user_schema"}
    ) or config.provider.user_schema not in {
        record.provider.user_schema,
        record.target.schema_version,
    }:
        raise ProviderError("Provider configuration changed during schema migration.")


def publish_schema_transition(
    entry: ContextEntry,
    config: SFTPWardenConfig,
    source: ProviderUsers,
    target: ProviderUsers,
    *,
    provider: BaseProvider,
    backup_path: str | None = None,
) -> SchemaTransition:
    """Journal a checked replacement, then publish its matching config.

    A failed second publication leaves the target snapshot in the journal. An
    explicit retry verifies that complete snapshot before completing the config;
    it never reconstructs v2 data from a lossy v1 SQL projection.
    """
    path = transition_path(entry)
    with state_transaction(Path(entry.config)):
        require_no_pending_transition(entry)
        current = load_config(entry.config)
        if current.provider != config.provider:
            raise ProviderError("Provider configuration changed before schema migration.")
        if target.schema_version < max(source.schema_version, current.provider.user_schema):
            raise ProviderError("Provider schema migrations are forward-only.")
        record = SchemaTransition(
            provider=current.provider,
            source=source,
            target=target,
            backup_path=backup_path,
        )
        write_private_text(path, record.model_dump_json())
        try:
            provider.replace_if_unchanged(source, target)
        except Exception:
            # A rejected write (e.g. standalone MongoDB bulk operations) must
            # not permanently block ordinary edits. Discard only when a fresh
            # complete read proves the source is unchanged. Uncertain effects,
            # partial DDL, and process interruption retain the recovery record.
            try:
                unchanged = _same_snapshot(read_schema_snapshot(provider), source)
            except Exception:
                unchanged = False
            if unchanged:
                path.unlink()
                sync_directory(path.parent)
            raise
        _finish_transition(entry, record)
        return record


def resume_schema_transition(entry: ContextEntry, config: SFTPWardenConfig) -> SchemaTransition:
    """Resume only an unchanged source or the exact previously published target."""
    with state_transaction(Path(entry.config)):
        record = pending_transition(entry)
        if record is None:
            raise ProviderError("No pending provider schema migration was found.")
        current = load_config(entry.config)
        check_transition_config(record, current)
        check_transition_config(record, config)
        source_config = current.model_copy(
            update={
                "provider": current.provider.model_copy(
                    update={"user_schema": record.source.schema_version}
                )
            }
        )
        provider = provider_from_config(entry.root, source_config)
        # Discovery distinguishes missing v2 storage from an already migrated
        # database. Divergent v2 data is a conflict even if its v1 projection
        # happens to equal the old source (e.g. password-only users).
        actual = read_schema_snapshot(provider)
        if not _same_snapshot(actual, record.target):
            if not _same_snapshot(actual, record.source):
                raise ProviderError(
                    "Provider changed during an incomplete schema migration.",
                    suggestion="Preserve the recovery record and reconcile the conflicting data.",
                )
            provider.replace_if_unchanged(record.source, record.target)
        _finish_transition(entry, record)
        return record


def _finish_transition(entry: ContextEntry, record: SchemaTransition) -> None:
    current = load_config(entry.config)
    check_transition_config(record, current)
    target_config = current.model_copy(
        update={
            "provider": current.provider.model_copy(
                update={"user_schema": record.target.schema_version}
            )
        }
    )
    actual = provider_from_config(entry.root, target_config).read()
    if not _same_snapshot(actual, record.target):
        raise ProviderError("Provider changed before schema migration could complete.")
    write_config(entry.config, target_config)
    path = transition_path(entry)
    path.unlink()
    sync_directory(path.parent)


def _same_snapshot(left: ProviderUsers, right: ProviderUsers) -> bool:
    """Compare complete policy and metadata, allowing storage's row/key ordering."""

    def ordered(users: ProviderUsers) -> ProviderUsers:
        return users.model_copy(
            update={
                "users": [
                    user.model_copy(
                        update={
                            "public_keys": sorted(user.public_keys)
                            if users.schema_version == 1
                            else [],
                            "keys": sorted(
                                user.key_objects() if users.schema_version >= 2 else user.keys,
                                key=lambda key: key.name,
                            ),
                        }
                    )
                    for user in sorted(users.users, key=lambda user: user.username)
                ]
            }
        )

    return ordered(left) == ordered(right)
