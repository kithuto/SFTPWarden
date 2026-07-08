from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, TypeVar

from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.users.schemas.base import UserSchemaVersion
from sftpwarden.users.schemas.registry import user_schema, validate_user_schema_version
from sftpwarden.utils.errors import ProviderError, RuntimeError

_MigrationT = TypeVar("_MigrationT", bound="UserSchemaMigration")
_MIGRATIONS: dict[tuple[UserSchemaVersion, UserSchemaVersion], type[UserSchemaMigration]] = {}


class UserSchemaMigration(ABC):
    """Forward-only provider user schema migration."""

    from_version: ClassVar[UserSchemaVersion]
    to_version: ClassVar[UserSchemaVersion]

    @abstractmethod
    def migrate(self, users: ProviderUsers) -> ProviderUsers:
        """Return users migrated to ``to_version``."""
        raise NotImplementedError


def register_user_schema_migration(
    migration_class: type[_MigrationT],
) -> type[_MigrationT]:
    """Register a provider user schema migration class."""
    _MIGRATIONS[(migration_class.from_version, migration_class.to_version)] = migration_class
    return migration_class


def migrate_provider_users(users: ProviderUsers, *, to_version: int) -> ProviderUsers:
    """Migrate provider users forward to a supported schema version."""
    source_version = validate_user_schema_version(users.schema_version)
    target_version = validate_user_schema_version(to_version)
    if source_version == target_version:
        return users
    if source_version > target_version:
        raise ProviderError(
            f"Cannot migrate provider schema backward from v{source_version} to v{target_version}."
        )
    current_users = users
    current_version = source_version
    while current_version != target_version:
        next_migration = _next_migration(current_version, target_version)
        current_users = next_migration.migrate(current_users)
        current_version = next_migration.to_version
    return current_users


def ensure_schema_capability(
    users: ProviderUsers,
    capability: str,
    *,
    allow_migration: bool,
    dry_run: bool,
    operation: str,
) -> tuple[ProviderUsers, bool]:
    """Return users with a schema that supports a required capability."""
    schema = user_schema(users.schema_version)
    if schema.supports(capability):
        return users, False
    target_version = first_schema_with_capability(capability, from_version=schema.version)
    if not allow_migration and not dry_run:
        raise RuntimeError(
            f"{operation} requires schema v{target_version} for named-key metadata.",
            suggestion=(f"Rerun with --yes to migrate this provider to schema v{target_version}."),
        )
    return migrate_provider_users(users, to_version=target_version), True


def first_schema_with_capability(capability: str, *, from_version: int = 1) -> int:
    """Return the first supported forward schema version with a capability."""
    source_version = validate_user_schema_version(from_version)
    for version in sorted(
        version for version in _supported_versions() if version >= source_version
    ):
        if user_schema(version).supports(capability):
            return version
    raise ProviderError(f"No supported provider user schema has capability: {capability}.")


def _supported_versions() -> tuple[int, ...]:
    from sftpwarden.users.schemas.registry import supported_user_schemas

    return supported_user_schemas()


def _next_migration(source_version: int, target_version: int) -> UserSchemaMigration:
    candidates = [
        migration_class
        for (from_version, to_version), migration_class in _MIGRATIONS.items()
        if from_version == source_version and source_version < to_version <= target_version
    ]
    if not candidates:
        raise ProviderError(
            f"No provider user schema migration path from v{source_version} to v{target_version}."
        )
    migration_class = sorted(candidates, key=lambda item: item.to_version)[0]
    return migration_class()


@register_user_schema_migration
class UserSchemaMigrationV1ToV2(UserSchemaMigration):
    """Migrate anonymous public_keys users to named-key schema v2."""

    from_version = 1
    to_version = 2

    def migrate(self, users: ProviderUsers) -> ProviderUsers:
        """Return a schema v2 representation of provider users."""
        migrated_users = []
        for user in users.users:
            data = user.model_dump(mode="python")
            data.update({"public_keys": [], "keys": user.key_objects()})
            migrated_users.append(SFTPUser.model_validate(data))
        return ProviderUsers(schema_version=self.to_version, users=migrated_users)
