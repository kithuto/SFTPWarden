from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, TypedDict

from sftpwarden.users.models import ProviderUsers, SFTPUser, SFTPUserKey

BASIC_PUBLIC_KEYS = "basic_public_keys"
NAMED_KEYS = "named_keys"
NAMED_KEY_METADATA = "named_key_metadata"
KEY_LIFECYCLE = "key_lifecycle"

UserSchemaVersion = int


class SFTPUserAuthFields(TypedDict):
    """Authentication fields accepted by ``SFTPUser`` construction."""

    public_keys: list[str]
    keys: list[SFTPUserKey]


class UserSchema(ABC):
    """Base strategy for provider user schema versions."""

    version: ClassVar[UserSchemaVersion]
    capabilities: ClassVar[frozenset[str]] = frozenset()
    include_schema_version: ClassVar[bool] = True

    def supports(self, capability: str) -> bool:
        """Return whether this schema supports a behavior capability."""
        return capability in self.capabilities

    @abstractmethod
    def empty_mapping(self) -> dict[str, Any]:
        """Return an empty YAML/JSON-like provider mapping."""
        raise NotImplementedError

    @abstractmethod
    def detect_mapping(self, data: dict[str, Any]) -> bool:
        """Return whether an unversioned mapping appears to use this schema."""
        raise NotImplementedError

    @abstractmethod
    def users_from_mapping(self, data: dict[str, Any]) -> ProviderUsers:
        """Deserialize users from a YAML/JSON-like provider mapping."""
        raise NotImplementedError

    @abstractmethod
    def users_to_mapping(self, users: ProviderUsers) -> dict[str, Any]:
        """Serialize users to a YAML/JSON-like provider mapping."""
        raise NotImplementedError

    @abstractmethod
    def user_to_mapping(self, user: SFTPUser) -> dict[str, Any]:
        """Serialize one user to this schema's mapping format."""
        raise NotImplementedError

    @abstractmethod
    def csv_fieldnames(self) -> list[str]:
        """Return the CSV fieldnames for this schema."""
        raise NotImplementedError

    @abstractmethod
    def detect_csv(self, fieldnames: list[str]) -> bool:
        """Return whether CSV fieldnames appear to use this schema."""
        raise NotImplementedError

    @abstractmethod
    def user_from_csv_row(self, row: dict[str, str]) -> SFTPUser:
        """Deserialize one CSV row."""
        raise NotImplementedError

    @abstractmethod
    def csv_row_from_user(self, user: SFTPUser) -> dict[str, str | int | bool | None]:
        """Serialize one user to a CSV row."""
        raise NotImplementedError

    @abstractmethod
    def auth_fields_from_public_keys(
        self,
        public_keys: list[str],
        *,
        source: str,
    ) -> SFTPUserAuthFields:
        """Return SFTPUser auth fields for public key input."""
        raise NotImplementedError

    @abstractmethod
    def add_key(
        self,
        user: SFTPUser,
        *,
        key_name: str,
        public_key: str,
        comment: str | None,
        source: str,
    ) -> SFTPUser:
        """Return a user with one key added according to this schema."""
        raise NotImplementedError

    @abstractmethod
    def remove_key(self, user: SFTPUser, key: SFTPUserKey) -> SFTPUser:
        """Return a user with one key removed according to this schema."""
        raise NotImplementedError
