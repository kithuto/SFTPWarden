from __future__ import annotations

import pytest

from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.users.schemas import (
    BASIC_PUBLIC_KEYS,
    KEY_LIFECYCLE,
    NAMED_KEYS,
    UserSchema,
    detect_csv_schema,
    detect_mapping_schema,
    ensure_schema_capability,
    first_schema_with_capability,
    migrate_provider_users,
    supported_user_schemas,
    user_schema,
    user_schema_class,
    users_from_mapping,
    users_to_mapping,
    validate_user_schema_version,
    with_schema,
)
from sftpwarden.users.schemas import migrations as schema_migrations
from sftpwarden.users.schemas import registry as schema_registry
from sftpwarden.users.schemas.migrations import UserSchemaMigration
from sftpwarden.users.schemas.v1 import UserSchemaV1
from sftpwarden.users.schemas.v2 import UserSchemaV2
from sftpwarden.utils.errors import ProviderError, RuntimeError

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"
TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"


def test_user_schema_registry_resolves_versions_and_capabilities() -> None:
    assert supported_user_schemas() == (1, 2)
    assert user_schema_class(1).version == 1
    assert user_schema(2).supports(NAMED_KEYS)
    assert user_schema(2).supports(KEY_LIFECYCLE)
    assert not user_schema(1).supports(KEY_LIFECYCLE)

    with pytest.raises(ProviderError, match="Unsupported provider user schema version"):
        user_schema(99)
    with pytest.raises(ProviderError, match="must be one of"):
        validate_user_schema_version("banana")


def test_user_schema_mapping_detection_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    class NeverDetectV1(UserSchemaV1):
        def detect_mapping(self, data):  # type: ignore[no-untyped-def]
            return False

        def detect_csv(self, fieldnames):  # type: ignore[no-untyped-def]
            return False

    class NeverDetectV2(UserSchemaV2):
        def detect_mapping(self, data):  # type: ignore[no-untyped-def]
            return False

        def detect_csv(self, fieldnames):  # type: ignore[no-untyped-def]
            return False

    v2_key = SFTPUserKey(name="prod-ci", public_key=TEST_KEY)
    assert detect_mapping_schema({"users": []}).version == 1
    assert (
        detect_mapping_schema(
            {"users": [{"username": "alice", "keys": [v2_key.model_dump(mode="json")]}]}
        ).version
        == 2
    )
    assert detect_mapping_schema({"schema_version": 2, "users": []}).version == 2
    assert detect_csv_schema(["username"]).version == 1
    monkeypatch.setattr(schema_registry, "_SCHEMAS", {1: NeverDetectV1, 2: NeverDetectV2})
    assert detect_mapping_schema({"items": []}, fallback_schema=2).version == 2
    assert detect_csv_schema(["username"], fallback_schema=2).version == 2
    assert users_to_mapping(
        ProviderUsers(schema_version=1, users=[]),
        schema_version=2,
    ) == {"schema_version": 2, "users": []}
    assert with_schema(ProviderUsers(schema_version=1, users=[]), 2).schema_version == 2

    with pytest.raises(ProviderError, match="Schema v1 users must use public_keys"):
        users_from_mapping(
            {
                "schema_version": 1,
                "users": [{"username": "alice", "keys": [v2_key.model_dump(mode="json")]}],
            }
        )
    with pytest.raises(ProviderError, match="Provider users must be mappings"):
        user_schema(1).users_from_mapping({"users": ["alice"]})
    with pytest.raises(ProviderError, match="Provider users must be mappings"):
        user_schema(2).users_from_mapping({"schema_version": 2, "users": ["alice"]})


def test_user_schema_v1_mapping_and_csv_round_trip() -> None:
    schema = user_schema(1)
    users = ProviderUsers(
        schema_version=1,
        users=[SFTPUser(username="alice", public_keys=[TEST_KEY], password_hash=TEST_HASH)],
    )
    mapping = schema.users_to_mapping(users)
    row = schema.csv_row_from_user(users.users[0])

    assert "schema_version" not in mapping
    assert mapping["users"][0]["username"] == "alice"
    assert mapping["users"][0]["public_keys"] == [TEST_KEY]
    assert row["public_keys"] == TEST_KEY
    assert schema.users_from_mapping(mapping) == users
    assert schema.user_from_csv_row(row).public_keys == [TEST_KEY]
    added = schema.add_key(
        users.users[0], key_name="ignored", public_key=TEST_KEY, comment=None, source="test"
    )
    removed = schema.remove_key(added, added.key_objects()[0])
    assert added.public_keys == [TEST_KEY, TEST_KEY]
    assert removed.public_keys == []


def test_user_schema_v2_mapping_and_csv_round_trip() -> None:
    schema = user_schema(2)
    key = SFTPUserKey(name="prod-ci", public_key=TEST_KEY, comment="CI deploy")
    users = ProviderUsers(
        schema_version=2,
        users=[SFTPUser(username="alice", keys=[key])],
    )
    mapping = schema.users_to_mapping(users)
    row = schema.csv_row_from_user(users.users[0])

    assert mapping["schema_version"] == 2
    assert mapping["users"][0]["keys"][0]["name"] == "prod-ci"
    assert '"name": "prod-ci"' in row["keys"]
    assert schema.users_from_mapping(mapping) == users
    assert schema.user_from_csv_row(row).keys[0].comment == "CI deploy"
    converted = schema.users_from_mapping(
        {"schema_version": 2, "users": [{"username": "bob", "public_keys": [TEST_KEY]}]}
    )
    added = schema.add_key(
        SFTPUser(username="carol", password_hash=TEST_HASH),
        key_name="laptop",
        public_key=TEST_KEY,
        comment="workstation",
        source="test",
    )
    removed = schema.remove_key(added, added.keys[0])
    assert converted.users[0].keys[0].name.startswith("key-")
    assert added.keys[0].comment == "workstation"
    assert removed.keys == []
    with pytest.raises(ProviderError, match="Invalid CSV keys JSON"):
        schema.user_from_csv_row({"username": "alice", "keys": "not-json"})
    with pytest.raises(ProviderError, match="CSV keys JSON must be a list"):
        schema.user_from_csv_row({"username": "alice", "keys": "{}"})


def test_user_schema_migrations_are_forward_only() -> None:
    users = ProviderUsers(
        schema_version=1,
        users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
    )

    migrated = migrate_provider_users(users, to_version=2)

    assert migrated.schema_version == 2
    assert migrated.users[0].keys[0].name.startswith("legacy-")
    with pytest.raises(ProviderError, match="Cannot migrate provider schema backward"):
        migrate_provider_users(migrated, to_version=1)
    with pytest.raises(ProviderError, match="Unsupported provider user schema version"):
        migrate_provider_users(
            ProviderUsers.model_construct(schema_version=99, users=[]),
            to_version=2,
        )
    assert migrate_provider_users(migrated, to_version=2) is migrated


def test_user_schema_migration_registry_reports_missing_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = ProviderUsers(
        schema_version=1,
        users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
    )
    monkeypatch.setattr(schema_migrations, "_MIGRATIONS", {})

    with pytest.raises(ProviderError, match="No provider user schema migration path"):
        migrate_provider_users(users, to_version=2)


def test_ensure_schema_capability_migrates_or_requires_confirmation() -> None:
    users = ProviderUsers(
        schema_version=1,
        users=[SFTPUser(username="alice", public_keys=[TEST_KEY])],
    )

    migrated, changed = ensure_schema_capability(
        users,
        KEY_LIFECYCLE,
        allow_migration=False,
        dry_run=True,
        operation="key rotate",
    )

    assert changed
    assert migrated.schema_version == 2
    same, same_changed = ensure_schema_capability(
        migrated,
        KEY_LIFECYCLE,
        allow_migration=False,
        dry_run=False,
        operation="key rotate",
    )
    assert same is migrated
    assert not same_changed
    assert first_schema_with_capability(BASIC_PUBLIC_KEYS, from_version=1) == 1
    with pytest.raises(ProviderError, match="No supported provider user schema"):
        first_schema_with_capability("warp_drive", from_version=1)
    with pytest.raises(RuntimeError, match="key rotate requires schema v2"):
        ensure_schema_capability(
            users,
            KEY_LIFECYCLE,
            allow_migration=False,
            dry_run=False,
            operation="key rotate",
        )


def test_user_schema_base_contract_methods_raise_not_implemented() -> None:
    users = ProviderUsers(schema_version=1, users=[])
    user = SFTPUser(username="alice", public_keys=[TEST_KEY])
    key = SFTPUserKey(name="prod", public_key=TEST_KEY)

    abstract_calls = [
        lambda: UserSchema.empty_mapping(object()),  # type: ignore[arg-type]
        lambda: UserSchema.detect_mapping(object(), {}),  # type: ignore[arg-type]
        lambda: UserSchema.users_from_mapping(object(), {}),  # type: ignore[arg-type]
        lambda: UserSchema.users_to_mapping(object(), users),  # type: ignore[arg-type]
        lambda: UserSchema.user_to_mapping(object(), user),  # type: ignore[arg-type]
        lambda: UserSchema.csv_fieldnames(object()),  # type: ignore[arg-type]
        lambda: UserSchema.detect_csv(object(), []),  # type: ignore[arg-type]
        lambda: UserSchema.user_from_csv_row(object(), {"username": "alice"}),  # type: ignore[arg-type]
        lambda: UserSchema.csv_row_from_user(object(), user),  # type: ignore[arg-type]
        lambda: UserSchema.auth_fields_from_public_keys(  # type: ignore[arg-type]
            object(),
            [TEST_KEY],
            source="test",
        ),
        lambda: UserSchema.add_key(  # type: ignore[arg-type]
            object(),
            user,
            key_name="prod",
            public_key=TEST_KEY,
            comment=None,
            source="test",
        ),
        lambda: UserSchema.remove_key(object(), user, key),  # type: ignore[arg-type]
        lambda: UserSchemaMigration.migrate(object(), users),  # type: ignore[arg-type]
    ]

    for call in abstract_calls:
        with pytest.raises(NotImplementedError):
            call()
