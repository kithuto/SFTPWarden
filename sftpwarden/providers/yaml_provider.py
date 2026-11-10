from __future__ import annotations

import yaml

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import FileProvider
from sftpwarden.providers.documents import parse_provider_document
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers
from sftpwarden.users.schemas import (
    UserSchemaVersion,
    user_schema,
    users_to_mapping,
)
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.persistence import atomic_bytes


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

    @classmethod
    def empty_text_for_schema(cls, schema_version: UserSchemaVersion) -> str:
        """Return an empty YAML provider document for a schema version."""
        return yaml.safe_dump(user_schema(schema_version).empty_mapping(), sort_keys=False)

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
        try:
            return parse_provider_document(path.read_text(encoding="utf-8"), "yaml")
        except (OSError, UnicodeError, ProviderError) as exc:
            raise ProviderError(f"Invalid YAML provider file: {path}.") from exc

    def _write(self, users: ProviderUsers) -> None:
        """Write users to a YAML provider file.

        Parameters
        ----------
        users
            Users to persist.
        """
        path = self.ensure_parent_dir()
        data = users_to_mapping(users)
        atomic_bytes(path, yaml.safe_dump(data, sort_keys=False).encode("utf-8"))
