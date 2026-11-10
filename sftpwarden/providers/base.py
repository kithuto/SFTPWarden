from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.users import service
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.persistence import (
    file_lock,
    migration_marker,
    pending_deploy_marker,
    reject_symlink,
)


class BaseProvider(ABC):
    """Base class for all user providers.

    Parameters
    ----------
    config
        Provider configuration.
    path
        Optional local provider file path.
    """

    provider_type: ProviderType
    mutable: bool = True

    def __init__(self, config: ProviderConfig, *, path: Path | None = None) -> None:
        self.config = config
        self.path = path

    @classmethod
    @abstractmethod
    def empty_text(cls) -> str:
        """Return an empty provider document.

        Returns
        -------
        str
            Provider-specific empty document text.
        """
        raise NotImplementedError

    @abstractmethod
    def read(self) -> ProviderUsers:
        """Read provider users.

        Returns
        -------
        ProviderUsers
            Users loaded from the provider.
        """
        raise NotImplementedError

    @abstractmethod
    def write(self, users: ProviderUsers) -> None:
        """Write a complete provider user set.

        Parameters
        ----------
        users
            Users to persist.
        """
        raise NotImplementedError

    def upsert_user(self, user: SFTPUser) -> None:
        """Create or replace a user.

        Parameters
        ----------
        user
            User to persist.
        """
        self.write(service.upsert_user(self.read(), user))

    def remove_user(self, username: str) -> None:
        """Remove a user by username.

        Parameters
        ----------
        username
            Username to remove.
        """
        self.write(service.remove_user(self.read(), username))

    def ensure_schema_storage(self, schema_version: int) -> None:
        """Ensure provider storage required by a user schema exists.

        Providers without schema-specific storage can leave this as a no-op.
        """
        return None

    def mutate_user(
        self,
        expected: ProviderUsers,
        username: str,
        replacement: SFTPUser | None,
        *,
        migrate_schema: bool = False,
    ) -> None:
        """Apply a checked single-user edit inside a provider-owned transaction."""
        raise ProviderError("This provider does not implement safe single-user mutations.")

    def replace_if_unchanged(self, expected: ProviderUsers, replacement: ProviderUsers) -> None:
        """Replace a complete provider only if its previously read snapshot still matches."""
        raise ProviderError("This provider does not implement checked provider replacement.")


class FileProvider(BaseProvider):
    """Base provider for file-backed user stores."""

    missing_file_suggestion = "Create it or run `sftpwarden init`."

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        """Exclude application writers and reject stale paths after deploy migration."""
        path = self.require_path()
        try:
            with file_lock(path):
                reject_symlink(path)
                if migration_marker(path).exists():
                    raise ProviderError(
                        "Provider path was migrated during deploy.",
                        suggestion="Reload the project config and edit its current provider path.",
                    )
                if pending_deploy_marker(path).exists():
                    raise ProviderError(
                        "Provider mount migration requires a successful deploy before user edits.",
                        suggestion="Retry `sftpwarden deploy` and wait for runtime readiness.",
                    )
                yield
        except OSError as exc:
            raise ProviderError(
                "Provider file operation failed.",
                suggestion="Inspect the current provider before retrying the operation.",
            ) from exc

    def write(self, users: ProviderUsers) -> None:
        """Serialize explicit complete replacement with all other application writers."""
        with self.writer_lock():
            self._write(users)

    @abstractmethod
    def _write(self, users: ProviderUsers) -> None:
        """Replace the provider while the stable writer lock is held."""
        raise NotImplementedError

    def upsert_user(self, user: SFTPUser) -> None:
        """Read and replace one user under the same process lock."""
        with self.writer_lock():
            self._upsert_user(user)

    def _upsert_user(self, user: SFTPUser) -> None:
        self._write(service.upsert_user(self.read(), user))

    def remove_user(self, username: str) -> None:
        """Read and remove one user under the same process lock."""
        with self.writer_lock():
            self._remove_user(username)

    def _remove_user(self, username: str) -> None:
        self._write(service.remove_user(self.read(), username))

    def mutate_user(
        self,
        expected: ProviderUsers,
        username: str,
        replacement: SFTPUser | None,
        *,
        migrate_schema: bool = False,
    ) -> None:
        """Compare and edit one user under the complete file read/write lock."""
        from sftpwarden.providers.mutations import prepare_user_mutation

        with self.writer_lock():
            merged, _ = prepare_user_mutation(
                self.read(), expected, username, replacement, migrate_schema=migrate_schema
            )
            self._write(merged)

    def replace_if_unchanged(self, expected: ProviderUsers, replacement: ProviderUsers) -> None:
        """Check a full import/schema snapshot while holding the stable file lock."""
        from sftpwarden.providers.mutations import require_unchanged_snapshot

        with self.writer_lock():
            require_unchanged_snapshot(self.read(), expected)
            self._write(replacement)

    def require_path(self) -> Path:
        """Return the configured provider path.

        Returns
        -------
        Path
            Provider file path.

        Raises
        ------
        ProviderError
            Raised when no path was configured.
        """
        if self.path is None:
            raise ProviderError(f"{self.provider_type.value.upper()} provider requires a path.")
        return self.path

    def ensure_exists(self) -> Path:
        """Return the provider path after checking that it exists.

        Returns
        -------
        Path
            Existing provider file path.

        Raises
        ------
        ProviderError
            Raised when the file does not exist.
        """
        path = self.require_path()
        if not path.exists():
            raise ProviderError(
                f"Provider file not found: {path}",
                suggestion=self.missing_file_suggestion,
            )
        return path

    def ensure_parent_dir(self) -> Path:
        """Create the provider parent directory and return the path.

        Returns
        -------
        Path
            Provider file path.
        """
        path = self.require_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
