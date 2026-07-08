from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.users import service
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


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


class FileProvider(BaseProvider):
    """Base provider for file-backed user stores."""

    missing_file_suggestion = "Create it or run `sftpwarden init`."

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
