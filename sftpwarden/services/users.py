from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from sftpwarden.config import load_config
from sftpwarden.contexts import ContextEntry, ContextType, resolve_context
from sftpwarden.providers import (
    SFTPUser,
    find_user,
    provider_from_config,
)
from sftpwarden.remote.deploy import remote_shell_command
from sftpwarden.system.commands import run_checked
from sftpwarden.users.models import USERNAME_RE, ProviderUsers
from sftpwarden.utils.errors import RuntimeError


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
        return self.provider.read()

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
        user = SFTPUser(
            username=username,
            public_keys=public_keys or [],
            password_hash=password_hash,
            upload_dir=upload_dir,
            comment=comment,
            uid=uid,
            gid=gid,
        )
        self.provider.upsert_user(user)
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
        existing = find_user(self.provider.read(), username)
        runtime_changed = any(
            value is not None
            for value in (public_keys, password_hash, upload_dir, uid, gid, disabled)
        )
        updated = existing.model_copy(
            update={
                "public_keys": public_keys if public_keys is not None else existing.public_keys,
                "password_hash": password_hash
                if password_hash is not None
                else existing.password_hash,
                "upload_dir": upload_dir if upload_dir is not None else existing.upload_dir,
                "comment": comment if comment is not None else existing.comment,
                "uid": uid if uid is not None else existing.uid,
                "gid": gid if gid is not None else existing.gid,
                "disabled": disabled if disabled is not None else existing.disabled,
            }
        )
        self.provider.upsert_user(updated)
        return UserUpdateResult(user=updated, runtime_changed=runtime_changed)

    def remove_user(self, username: str) -> None:
        """Remove a provider user.

        Parameters
        ----------
        username
            Username to remove.
        """
        self.provider.remove_user(username)

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
