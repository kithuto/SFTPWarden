from __future__ import annotations

from typing import Any, TypeVar

from sftpwarden.users.models import ProviderUsers
from sftpwarden.users.schemas.base import UserSchema, UserSchemaVersion
from sftpwarden.utils.errors import ProviderError

_SchemaT = TypeVar("_SchemaT", bound=UserSchema)
UserSchemaClass = type[UserSchema]
_SCHEMAS: dict[UserSchemaVersion, UserSchemaClass] = {}
_BUILTINS_LOADED = False


def _load_builtin_schemas() -> None:
    """Import builtin schemas once so decorator registration has run."""
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    import sftpwarden.users.schemas.v1  # noqa: F401
    import sftpwarden.users.schemas.v2  # noqa: F401


def register_user_schema(schema_class: type[_SchemaT]) -> type[_SchemaT]:
    """Register a provider user schema class."""
    _SCHEMAS[schema_class.version] = schema_class
    return schema_class


def supported_user_schemas() -> tuple[UserSchemaVersion, ...]:
    """Return supported user schema versions."""
    _load_builtin_schemas()
    return tuple(sorted(_SCHEMAS))


def validate_user_schema_version(value: Any) -> UserSchemaVersion:
    """Validate a provider user schema version."""
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(str(item) for item in supported_user_schemas())
        raise ProviderError(f"Provider user schema version must be one of: {choices}.") from exc
    if version not in supported_user_schemas():
        raise ProviderError(
            f"Unsupported provider user schema version: {version}.",
            suggestion="Upgrade SFTPWarden before reading providers created by a newer version.",
        )
    return version


def user_schema_class(version: UserSchemaVersion | str) -> UserSchemaClass:
    """Return the registered class for a user schema version."""
    normalized = validate_user_schema_version(version)
    return _SCHEMAS[normalized]


def user_schema(version: UserSchemaVersion | str) -> UserSchema:
    """Return a user schema strategy instance."""
    return user_schema_class(version)()


def detect_mapping_schema(
    data: dict[str, Any],
    *,
    fallback_schema: UserSchemaVersion = 1,
) -> UserSchema:
    """Detect a mapping-backed provider schema without mutating data."""
    if "schema_version" in data:
        return user_schema(validate_user_schema_version(data["schema_version"]))
    _load_builtin_schemas()
    for version in sorted(_SCHEMAS, reverse=True):
        schema = user_schema(version)
        if schema.detect_mapping(data):
            return schema
    return user_schema(fallback_schema)


def users_from_mapping(
    data: dict[str, Any],
    *,
    fallback_schema: UserSchemaVersion = 1,
) -> ProviderUsers:
    """Build provider users from a YAML/JSON-like mapping."""
    return detect_mapping_schema(data, fallback_schema=fallback_schema).users_from_mapping(data)


def users_to_mapping(
    users: ProviderUsers,
    *,
    schema_version: UserSchemaVersion | None = None,
) -> dict[str, Any]:
    """Serialize provider users for YAML/JSON-like storage."""
    schema = user_schema(schema_version or users.schema_version)
    return schema.users_to_mapping(users)


def detect_csv_schema(
    fieldnames: list[str],
    *,
    fallback_schema: UserSchemaVersion = 1,
) -> UserSchema:
    """Detect a CSV-backed provider schema from fieldnames."""
    _load_builtin_schemas()
    for version in sorted(_SCHEMAS, reverse=True):
        schema = user_schema(version)
        if schema.detect_csv(fieldnames):
            return schema
    return user_schema(fallback_schema)


def with_schema(users: ProviderUsers, schema_version: UserSchemaVersion) -> ProviderUsers:
    """Return the same users marked with a provider schema version."""
    return ProviderUsers(
        schema_version=validate_user_schema_version(schema_version), users=users.users
    )
