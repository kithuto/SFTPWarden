from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sftpwarden.config import load_config
from sftpwarden.contexts import ContextEntry, ContextType, resolve_context
from sftpwarden.providers import (
    SFTPUser,
    find_user,
    provider_from_config,
)
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.remote.deploy import remote_shell_command
from sftpwarden.services.provider_schema import write_provider_schema_backup
from sftpwarden.services.schema_transition import (
    publish_schema_transition,
    require_no_pending_transition,
)
from sftpwarden.system.commands import run_checked
from sftpwarden.users.models import (
    USERNAME_RE,
    ProviderUsers,
    SFTPUserKey,
    parse_datetime,
    public_key_fingerprint,
)
from sftpwarden.users.schemas import (
    KEY_LIFECYCLE,
    ensure_schema_capability,
)
from sftpwarden.users.schemas import (
    user_schema as user_schema_for,
)
from sftpwarden.users.schemas.base import SFTPUserAuthFields
from sftpwarden.users.service import upsert_user
from sftpwarden.utils.errors import ProviderError, RuntimeError
from sftpwarden.utils.state_transactions import state_transaction


@dataclass(frozen=True)
class UserUpdateResult:
    """Result returned when a user is updated.

    Attributes
    ----------
    user
        Updated user model.
    runtime_changed
        Whether the change affects runtime state and should trigger refresh.
    """

    user: SFTPUser
    runtime_changed: bool


@dataclass(frozen=True)
class UserKeyMutationResult:
    """Result returned by user key lifecycle mutations."""

    user: SFTPUser
    schema_migrated: bool
    runtime_changed: bool = True
    dry_run: bool = False


class UserService:
    """Application service for provider user operations.

    Parameters
    ----------
    context_name
        Optional context name to resolve.
    config_path
        Optional explicit project config path.
    """

    def __init__(self, *, context_name: str | None = None, config_path: str | None = None) -> None:
        self.entry = resolve_context(config_path=config_path, context_name=context_name)
        self.config = load_config(self.entry.config)
        self.provider = provider_from_config(self.entry.root, self.config)

    @property
    def context(self) -> ContextEntry:
        """Return the resolved context.

        Returns
        -------
        ContextEntry
            Context used by this service.
        """
        return self.entry

    def list_users(self) -> ProviderUsers:
        """List provider users.

        Returns
        -------
        ProviderUsers
            Users currently stored in the provider.
        """
        require_no_pending_transition(self.entry)
        return require_configured_schema(self.provider)

    def show_user(self, username: str) -> SFTPUser:
        """Return a single provider user.

        Parameters
        ----------
        username
            Username to find.

        Returns
        -------
        SFTPUser
            Matching user.
        """
        return find_user(self.list_users(), username)

    def add_user(
        self,
        *,
        username: str,
        public_keys: list[str] | None = None,
        password_hash: str | None = None,
        upload_dir: str = "upload",
        comment: str | None = None,
        uid: int | None = None,
        gid: int | None = None,
    ) -> SFTPUser:
        """Create a provider user.

        Parameters
        ----------
        username
            Username to create.
        public_keys
            Optional SSH public keys.
        password_hash
            Optional system password hash.
        upload_dir
            Relative upload directory.
        comment
            Optional user comment.
        uid
            Optional explicit UID.
        gid
            Optional explicit GID.

        Returns
        -------
        SFTPUser
            Created user.
        """
        if not public_keys and not password_hash:
            raise RuntimeError("User requires at least one public key or a password_hash.")
        users = self.list_users()
        schema = user_schema_for(users.schema_version)
        auth_fields = schema.auth_fields_from_public_keys(
            public_keys or [],
            source="user.create",
        )
        user = SFTPUser(
            username=username,
            password_hash=password_hash,
            upload_dir=upload_dir,
            comment=comment,
            uid=uid,
            gid=gid,
            **auth_fields,
        )
        self._save_user_mutation(users, username, user)
        return user

    def update_user(
        self,
        username: str,
        *,
        public_keys: list[str] | None = None,
        password_hash: str | None = None,
        upload_dir: str | None = None,
        comment: str | None = None,
        uid: int | None = None,
        gid: int | None = None,
        disabled: bool | None = None,
    ) -> UserUpdateResult:
        """Update a provider user.

        Parameters
        ----------
        username
            Username to update.
        public_keys
            Replacement SSH public keys.
        password_hash
            Replacement password hash.
        upload_dir
            Replacement upload directory.
        comment
            Replacement user comment.
        uid
            Replacement UID.
        gid
            Replacement GID.
        disabled
            Replacement disabled flag.

        Returns
        -------
        UserUpdateResult
            Updated user and runtime-change flag.
        """
        users = self.list_users()
        existing = find_user(users, username)
        runtime_changed = any(
            value is not None
            for value in (public_keys, password_hash, upload_dir, uid, gid, disabled)
        )
        auth_fields = SFTPUserAuthFields(
            public_keys=existing.public_keys,
            keys=existing.keys,
        )
        if public_keys is not None:
            auth_fields = user_schema_for(users.schema_version).auth_fields_from_public_keys(
                public_keys,
                source="user.update",
            )
        updated = copy_user(
            existing,
            public_keys=auth_fields["public_keys"],
            keys=auth_fields["keys"],
            password_hash=password_hash if password_hash is not None else existing.password_hash,
            upload_dir=upload_dir if upload_dir is not None else existing.upload_dir,
            comment=comment if comment is not None else existing.comment,
            uid=uid if uid is not None else existing.uid,
            gid=gid if gid is not None else existing.gid,
            disabled=disabled if disabled is not None else existing.disabled,
        )
        self._save_user_mutation(users, username, updated)
        return UserUpdateResult(user=updated, runtime_changed=runtime_changed)

    def set_user_disabled(self, username: str, *, disabled: bool) -> UserUpdateResult:
        """Enable or disable one user."""
        return self.update_user(username, disabled=disabled)

    def remove_user(self, username: str) -> None:
        """Remove a provider user.

        Parameters
        ----------
        username
            Username to remove.
        """
        users = self.list_users()
        find_user(users, username)
        self._save_user_mutation(users, username, None)

    def list_user_keys(self, username: str) -> list[SFTPUserKey]:
        """Return keys for one user, deriving virtual keys for schema v1."""
        return self.show_user(username).key_objects()

    def show_user_key(self, username: str, key_name: str) -> SFTPUserKey:
        """Return one user key by name or fingerprint."""
        user = self.show_user(username)
        return find_key(user, key_name)

    def add_user_key(
        self,
        username: str,
        *,
        key_name: str,
        public_key: str,
        comment: str | None = None,
        source: str | None = None,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Add one public key to a user."""
        users = self.list_users()
        user = find_user(users, username)
        schema = user_schema_for(users.schema_version)
        updated = schema.add_key(
            user,
            key_name=key_name,
            public_key=public_key,
            comment=comment,
            source=source or "user.key.add",
        )
        if not dry_run:
            self._save_user_mutation(users, username, updated)
        return UserKeyMutationResult(user=updated, schema_migrated=False, dry_run=dry_run)

    def remove_user_key(
        self,
        username: str,
        key_name: str,
        *,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Remove one key from a user."""
        users = self.list_users()
        user = find_user(users, username)
        key = find_key(user, key_name)
        updated = user_schema_for(users.schema_version).remove_key(user, key)
        if not dry_run:
            self._save_user_mutation(users, username, updated)
        return UserKeyMutationResult(user=updated, schema_migrated=False, dry_run=dry_run)

    def disable_user_key(
        self,
        username: str,
        key_name: str,
        *,
        disabled: bool,
        allow_migration: bool = False,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Enable or disable one named key, migrating v1 when allowed."""
        source, users, migrated = self._schema_users_with_key_lifecycle(
            allow_migration=allow_migration,
            dry_run=dry_run,
            operation="key disable/enable",
        )
        user = find_user(users, username)
        key = find_key(user, key_name)
        keys = [
            copy_key(item, disabled=disabled, updated_at=datetime.now(UTC))
            if item.name == key.name
            else item
            for item in user.key_objects()
        ]
        updated = copy_user(user, public_keys=[], keys=keys)
        if not dry_run:
            self._save_key_mutation(source, users, updated, migrated=migrated)
        return UserKeyMutationResult(user=updated, schema_migrated=migrated, dry_run=dry_run)

    def rename_user_key(
        self,
        username: str,
        old_name: str,
        new_name: str,
        *,
        allow_migration: bool = False,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Rename one key, migrating v1 when allowed."""
        source, users, migrated = self._schema_users_with_key_lifecycle(
            allow_migration=allow_migration,
            dry_run=dry_run,
            operation="key rename",
        )
        user = find_user(users, username)
        key = find_key(user, old_name)
        keys = [
            copy_key(item, name=new_name, updated_at=datetime.now(UTC))
            if item.name == key.name
            else item
            for item in user.key_objects()
        ]
        updated = copy_user(user, public_keys=[], keys=keys)
        if not dry_run:
            self._save_key_mutation(source, users, updated, migrated=migrated)
        return UserKeyMutationResult(user=updated, schema_migrated=migrated, dry_run=dry_run)

    def rotate_user_key(
        self,
        username: str,
        key_name: str,
        *,
        public_key: str,
        allow_migration: bool = False,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Replace one key's public key, migrating v1 when allowed."""
        source, users, migrated = self._schema_users_with_key_lifecycle(
            allow_migration=allow_migration,
            dry_run=dry_run,
            operation="key rotate",
        )
        user = find_user(users, username)
        key = find_key(user, key_name)
        keys = [
            copy_key(
                item,
                public_key=public_key,
                fingerprint=public_key_fingerprint(public_key),
                updated_at=datetime.now(UTC),
            )
            if item.name == key.name
            else item
            for item in user.key_objects()
        ]
        updated = copy_user(user, public_keys=[], keys=keys)
        if not dry_run:
            self._save_key_mutation(source, users, updated, migrated=migrated)
        return UserKeyMutationResult(user=updated, schema_migrated=migrated, dry_run=dry_run)

    def expire_user_key(
        self,
        username: str,
        key_name: str,
        *,
        expires_at: str,
        allow_migration: bool = False,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Set one key expiration timestamp, migrating v1 when allowed."""
        source, users, migrated = self._schema_users_with_key_lifecycle(
            allow_migration=allow_migration,
            dry_run=dry_run,
            operation="key expire",
        )
        user = find_user(users, username)
        key = find_key(user, key_name)
        parsed_expires_at = parse_datetime(expires_at)
        keys = [
            copy_key(item, expires_at=parsed_expires_at, updated_at=datetime.now(UTC))
            if item.name == key.name
            else item
            for item in user.key_objects()
        ]
        updated = copy_user(user, public_keys=[], keys=keys)
        if not dry_run:
            self._save_key_mutation(source, users, updated, migrated=migrated)
        return UserKeyMutationResult(user=updated, schema_migrated=migrated, dry_run=dry_run)

    def import_user_keys(
        self,
        username: str,
        key_files: list[tuple[str, str]],
        *,
        allow_migration: bool = False,
        dry_run: bool = False,
    ) -> UserKeyMutationResult:
        """Import named keys from file names and key contents."""
        source, users, migrated = self._schema_users_with_key_lifecycle(
            allow_migration=allow_migration,
            dry_run=dry_run,
            operation="key import",
        )
        user = find_user(users, username)
        now = datetime.now(UTC)
        imported = [
            SFTPUserKey(
                name=name,
                public_key=public_key,
                source="user.key.import",
                created_at=now,
                updated_at=now,
            )
            for name, public_key in key_files
        ]
        updated = copy_user(user, public_keys=[], keys=[*user.key_objects(), *imported])
        if not dry_run:
            self._save_key_mutation(source, users, updated, migrated=migrated)
        return UserKeyMutationResult(user=updated, schema_migrated=migrated, dry_run=dry_run)

    def _schema_users_with_key_lifecycle(
        self,
        *,
        allow_migration: bool,
        dry_run: bool,
        operation: str,
    ) -> tuple[ProviderUsers, ProviderUsers, bool]:
        """Load users and ensure their schema supports key lifecycle operations."""
        users = self.list_users()
        target, migrated = ensure_schema_capability(
            users,
            KEY_LIFECYCLE,
            allow_migration=allow_migration,
            dry_run=dry_run,
            operation=operation,
        )
        return users, target, migrated

    def _save_key_mutation(
        self,
        source: ProviderUsers,
        users: ProviderUsers,
        updated: SFTPUser,
        *,
        migrated: bool,
    ) -> None:
        if not migrated:
            self._save_user_mutation(users, updated.username, updated)
            return
        backup = write_provider_schema_backup(self.entry.root, source)
        publish_schema_transition(
            self.entry,
            self.config,
            source,
            upsert_user(users, updated),
            provider=self.provider,
            backup_path=str(backup),
        )
        self.config = load_config(self.entry.config)
        self.provider = provider_from_config(self.entry.root, self.config)

    def _save_user_mutation(
        self,
        users: ProviderUsers,
        username: str,
        replacement: SFTPUser | None,
    ) -> None:
        """Keep the checked provider write inside the schema/config transaction."""
        with state_transaction(Path(self.entry.config)):
            require_no_pending_transition(self.entry)
            if load_config(self.entry.config).provider != self.config.provider:
                raise ProviderError(
                    "Provider configuration changed before the user edit.",
                    suggestion="Reload the project configuration and retry the edit.",
                )
            self.provider.mutate_user(users, username, replacement)

    def delete_user_files(self, username: str) -> str:
        """Delete all runtime data files for a user.

        Parameters
        ----------
        username
            Username whose data directory should be deleted.

        Returns
        -------
        str
            Human-readable deletion result.
        """
        if not USERNAME_RE.fullmatch(username):
            raise RuntimeError("Invalid username for data deletion.")
        if self.entry.type == ContextType.LOCAL:
            data_path = Path(self.entry.root) / "data" / username
            if not data_path.exists():
                return f"No data directory found for {username}."
            shutil.rmtree(data_path)
            return f"Deleted data directory for {username}: {data_path}"
        if not self.entry.remote:
            raise RuntimeError(f"Context {self.entry.name} is missing remote settings.")
        remote_data = f"{self.entry.remote.remote_root.rstrip('/')}/data/{username}"
        command = remote_shell_command(
            self.entry.remote,
            f"rm -rf -- {shlex.quote(remote_data)}",
        )
        run_checked(
            command,
            error_type=RuntimeError,
            message=f"Failed to delete remote data directory for {username}.",
            fallback_suggestion="Verify SSH access and remote filesystem permissions.",
        )
        return f"Deleted remote data directory for {username}: {remote_data}"


def copy_user(user: SFTPUser, **updates: object) -> SFTPUser:
    """Copy and revalidate a user model."""
    data = user.model_dump(mode="python")
    data.update(updates)
    return SFTPUser.model_validate(data)


def copy_key(key: SFTPUserKey, **updates: object) -> SFTPUserKey:
    """Copy and revalidate a key model."""
    data = key.model_dump(mode="python")
    data.update(updates)
    return SFTPUserKey.model_validate(data)


def upsert_provider_user(users: ProviderUsers, user: SFTPUser) -> ProviderUsers:
    """Return provider users with one user inserted or replaced."""
    next_users = [existing for existing in users.users if existing.username != user.username]
    next_users.append(user)
    next_users.sort(key=lambda item: item.username)
    return ProviderUsers(schema_version=users.schema_version, users=next_users)


def find_key(user: SFTPUser, identifier: str) -> SFTPUserKey:
    """Find a key by name, full fingerprint, or SHA256 fingerprint body."""
    normalized_identifier = identifier.strip()
    for key in user.key_objects():
        fingerprint = key.fingerprint or ""
        fingerprint_body = fingerprint.removeprefix("SHA256:")
        if normalized_identifier in {key.name, fingerprint, fingerprint_body}:
            return key
    raise RuntimeError(
        f"Unknown key for user {user.username}: {identifier}",
        suggestion=f"Run `sftpwarden user key list {user.username}`.",
    )
