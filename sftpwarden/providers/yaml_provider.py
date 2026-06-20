from __future__ import annotations

import yaml
from pydantic import ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import FileProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import write_private_text


@register_provider
class YAMLProvider(FileProvider):
    """YAML-backed user provider."""

    provider_type = ProviderType.YAML

    @classmethod
    def empty_text(cls) -> str:
        """Return an empty YAML provider document.

        Returns
        -------
        str
            YAML text containing an empty users list.
        """
        return yaml.safe_dump({"users": []}, sort_keys=False)

    def read(self) -> ProviderUsers:
        """Read users from a YAML provider file.

        Returns
        -------
        ProviderUsers
            Parsed provider users.

        Raises
        ------
        ProviderError
            Raised when the file is missing or invalid.
        """
        path = self.ensure_exists()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"users": []}
        try:
            return ProviderUsers.model_validate(data)
        except ValidationError as exc:
            raise ProviderError(f"Invalid YAML provider file: {path}: {exc}") from exc

    def write(self, users: ProviderUsers) -> None:
        """Write users to a YAML provider file.

        Parameters
        ----------
        users
            Users to persist.
        """
        path = self.ensure_parent_dir()
        data = {"users": [user.model_dump(mode="json", exclude_none=True) for user in users.users]}
        write_private_text(path, yaml.safe_dump(data, sort_keys=False))
