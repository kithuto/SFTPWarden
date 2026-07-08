from __future__ import annotations

from typing import Any

from sftpwarden.users.models import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.users.schemas.base import BASIC_PUBLIC_KEYS, UserSchema
from sftpwarden.users.schemas.registry import register_user_schema
from sftpwarden.utils.errors import ProviderError

CSV_V1_FIELDNAMES = [
    "username",
    "public_keys",
    "password_hash",
    "uid",
    "gid",
    "upload_dir",
    "comment",
    "disabled",
]


@register_user_schema
class UserSchemaV1(UserSchema):
    """Simple public_keys user schema."""

    version = 1
    capabilities = frozenset({BASIC_PUBLIC_KEYS})
    include_schema_version = False

    def empty_mapping(self) -> dict[str, Any]:
        """Return an empty schema v1 provider mapping."""
        return {"users": []}

    def detect_mapping(self, data: dict[str, Any]) -> bool:
        """Return whether an unversioned mapping appears to be schema v1."""
        users = data.get("users") or []
        return not any(isinstance(user, dict) and user.get("keys") for user in users)

    def users_from_mapping(self, data: dict[str, Any]) -> ProviderUsers:
        """Deserialize schema v1 users from a mapping."""
        raw_users = data.get("users") or []
        normalized_users: list[dict[str, Any]] = []
        for raw_user in raw_users:
            if not isinstance(raw_user, dict):
                raise ProviderError("Provider users must be mappings.")
            record = dict(raw_user)
            if record.get("keys"):
                raise ProviderError("Schema v1 users must use public_keys, not keys.")
            record.pop("keys", None)
            normalized_users.append(record)
        return ProviderUsers.model_validate(
            {"schema_version": self.version, "users": normalized_users}
        )

    def users_to_mapping(self, users: ProviderUsers) -> dict[str, Any]:
        """Serialize provider users as schema v1."""
        return {"users": [self.user_to_mapping(user) for user in users.users]}

    def user_to_mapping(self, user: SFTPUser) -> dict[str, Any]:
        """Serialize one user as schema v1."""
        data = user.model_dump(mode="json", exclude_none=True, exclude={"keys"})
        data["public_keys"] = [key.public_key for key in user.key_objects()]
        return data

    def csv_fieldnames(self) -> list[str]:
        """Return schema v1 CSV fieldnames."""
        return list(CSV_V1_FIELDNAMES)

    def detect_csv(self, fieldnames: list[str]) -> bool:
        """Return whether CSV fieldnames appear to be schema v1."""
        return "keys" not in fieldnames

    def user_from_csv_row(self, row: dict[str, str]) -> SFTPUser:
        """Deserialize one schema v1 CSV row."""
        public_keys = [
            key.strip() for key in (row.get("public_keys") or "").splitlines() if key.strip()
        ]
        return SFTPUser(
            username=row["username"],
            public_keys=public_keys,
            password_hash=row.get("password_hash") or None,
            uid=int(row["uid"]) if row.get("uid") else None,
            gid=int(row["gid"]) if row.get("gid") else None,
            upload_dir=row.get("upload_dir") or "upload",
            comment=row.get("comment") or None,
            disabled=(row.get("disabled") or "").lower() in {"1", "true", "yes"},
        )

    def csv_row_from_user(self, user: SFTPUser) -> dict[str, str | int | bool | None]:
        """Serialize one user to a schema v1 CSV row."""
        data = self.user_to_mapping(user)
        row = {field: data.get(field, "") for field in self.csv_fieldnames()}
        row["public_keys"] = "\n".join(data.get("public_keys") or [])
        return row

    def auth_fields_from_public_keys(
        self,
        public_keys: list[str],
        *,
        source: str,
    ) -> dict[str, object]:
        """Return schema v1 auth fields for public key input."""
        return {"public_keys": public_keys, "keys": []}

    def add_key(
        self,
        user: SFTPUser,
        *,
        key_name: str,
        public_key: str,
        comment: str | None,
        source: str,
    ) -> SFTPUser:
        """Append one anonymous public key to a schema v1 user."""
        data = user.model_dump(mode="python")
        data.update({"public_keys": [*user.public_keys, public_key], "keys": []})
        return SFTPUser.model_validate(data)

    def remove_key(self, user: SFTPUser, key: SFTPUserKey) -> SFTPUser:
        """Remove one public key from a schema v1 user."""
        data = user.model_dump(mode="python")
        data.update(
            {
                "public_keys": [
                    public_key for public_key in user.public_keys if public_key != key.public_key
                ],
                "keys": [],
            }
        )
        return SFTPUser.model_validate(data)
