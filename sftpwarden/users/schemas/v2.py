from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sftpwarden.users.models import (
    ProviderUsers,
    SFTPUser,
    SFTPUserKey,
    deterministic_key_name,
)
from sftpwarden.users.schemas.base import (
    BASIC_PUBLIC_KEYS,
    KEY_LIFECYCLE,
    NAMED_KEY_METADATA,
    NAMED_KEYS,
    UserSchema,
)
from sftpwarden.users.schemas.registry import register_user_schema
from sftpwarden.utils.errors import ProviderError

CSV_V2_FIELDNAMES = [
    "username",
    "keys",
    "password_hash",
    "uid",
    "gid",
    "upload_dir",
    "comment",
    "disabled",
]


@register_user_schema
class UserSchemaV2(UserSchema):
    """Advanced named-key user schema."""

    version = 2
    capabilities = frozenset({BASIC_PUBLIC_KEYS, NAMED_KEYS, NAMED_KEY_METADATA, KEY_LIFECYCLE})

    def empty_mapping(self) -> dict[str, Any]:
        """Return an empty schema v2 provider mapping."""
        return {"schema_version": self.version, "users": []}

    def detect_mapping(self, data: dict[str, Any]) -> bool:
        """Return whether an unversioned mapping appears to be schema v2."""
        users = data.get("users") or []
        return any(isinstance(user, dict) and user.get("keys") for user in users)

    def users_from_mapping(self, data: dict[str, Any]) -> ProviderUsers:
        """Deserialize schema v2 users from a mapping."""
        raw_users = data.get("users") or []
        normalized_users: list[dict[str, Any]] = []
        for raw_user in raw_users:
            if not isinstance(raw_user, dict):
                raise ProviderError("Provider users must be mappings.")
            record = dict(raw_user)
            keys = record.get("keys") or []
            if not keys and record.get("public_keys"):
                keys = [
                    SFTPUserKey(
                        name=deterministic_key_name(public_key, prefix="key"),
                        public_key=public_key,
                        source="public_keys",
                    ).model_dump(mode="json", exclude_none=True)
                    for public_key in record.get("public_keys") or []
                ]
            record["keys"] = keys
            normalized_users.append(record)
        return ProviderUsers.model_validate(
            {"schema_version": self.version, "users": normalized_users}
        )

    def users_to_mapping(self, users: ProviderUsers) -> dict[str, Any]:
        """Serialize provider users as schema v2."""
        return {
            "schema_version": self.version,
            "users": [self.user_to_mapping(user) for user in users.users],
        }

    def user_to_mapping(self, user: SFTPUser) -> dict[str, Any]:
        """Serialize one user as schema v2."""
        data = user.model_dump(mode="json", exclude_none=True, exclude={"public_keys"})
        data["keys"] = [
            key.model_dump(mode="json", exclude_none=True) for key in user.key_objects()
        ]
        return data

    def csv_fieldnames(self) -> list[str]:
        """Return schema v2 CSV fieldnames."""
        return list(CSV_V2_FIELDNAMES)

    def detect_csv(self, fieldnames: list[str]) -> bool:
        """Return whether CSV fieldnames appear to be schema v2."""
        return "keys" in fieldnames

    def user_from_csv_row(self, row: dict[str, str]) -> SFTPUser:
        """Deserialize one schema v2 CSV row."""
        try:
            raw_keys = json.loads(row.get("keys") or "[]")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Invalid CSV keys JSON for user {row['username']}.") from exc
        if not isinstance(raw_keys, list):
            raise ProviderError(f"CSV keys JSON must be a list for user {row['username']}.")
        keys = [SFTPUserKey.model_validate(raw_key) for raw_key in raw_keys]
        return SFTPUser(
            username=row["username"],
            public_keys=[],
            keys=keys,
            password_hash=row.get("password_hash") or None,
            uid=int(row["uid"]) if row.get("uid") else None,
            gid=int(row["gid"]) if row.get("gid") else None,
            upload_dir=row.get("upload_dir") or "upload",
            comment=row.get("comment") or None,
            disabled=(row.get("disabled") or "").lower() in {"1", "true", "yes"},
        )

    def csv_row_from_user(self, user: SFTPUser) -> dict[str, str | int | bool | None]:
        """Serialize one user to a schema v2 CSV row."""
        data = self.user_to_mapping(user)
        row = {field: data.get(field, "") for field in self.csv_fieldnames()}
        row["keys"] = json.dumps(data.get("keys") or [], sort_keys=True)
        return row

    def auth_fields_from_public_keys(
        self,
        public_keys: list[str],
        *,
        source: str,
    ) -> dict[str, object]:
        """Return schema v2 auth fields for public key input."""
        return {
            "public_keys": [],
            "keys": [
                SFTPUserKey(
                    name=deterministic_key_name(public_key, prefix="key"),
                    public_key=public_key,
                    source=source,
                )
                for public_key in public_keys
            ],
        }

    def add_key(
        self,
        user: SFTPUser,
        *,
        key_name: str,
        public_key: str,
        comment: str | None,
        source: str,
    ) -> SFTPUser:
        """Add one named key to a schema v2 user."""
        now = datetime.now(UTC)
        key = SFTPUserKey(
            name=key_name,
            public_key=public_key,
            comment=comment,
            source=source,
            created_at=now,
            updated_at=now,
        )
        data = user.model_dump(mode="python")
        data.update({"public_keys": [], "keys": [*user.key_objects(), key]})
        return SFTPUser.model_validate(data)

    def remove_key(self, user: SFTPUser, key: SFTPUserKey) -> SFTPUser:
        """Remove one named key from a schema v2 user."""
        data = user.model_dump(mode="python")
        data.update(
            {
                "public_keys": [],
                "keys": [item for item in user.key_objects() if item.name != key.name],
            }
        )
        return SFTPUser.model_validate(data)
