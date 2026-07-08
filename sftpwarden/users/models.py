from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sftpwarden.utils.validation import validate_relative_safe_path

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEY_NAME_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
PUBLIC_KEY_PREFIXES = (
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "sk-ecdsa-sha2-nistp256@openssh.com ",
    "sk-ssh-ed25519@openssh.com ",
    "ssh-ed25519 ",
    "ssh-rsa ",
)


def normalize_public_key(value: str) -> str:
    """Validate and normalize an SSH public key string."""
    stripped = value.strip()
    if not stripped.startswith(PUBLIC_KEY_PREFIXES):
        raise ValueError("Unsupported or invalid SSH public key.")
    parts = stripped.split()
    try:
        base64.b64decode(parts[1].encode("ascii"), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Unsupported or invalid SSH public key.") from exc
    return stripped


def public_key_fingerprint(public_key: str) -> str:
    """Return the OpenSSH-compatible SHA256 fingerprint for a public key."""
    normalized = normalize_public_key(public_key)
    key_blob = base64.b64decode(normalized.split()[1].encode("ascii"), validate=True)
    digest = base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def deterministic_key_name(public_key: str, *, prefix: str = "legacy") -> str:
    """Return a deterministic safe key name for an anonymous public key."""
    digest = hashlib.sha256(normalize_public_key(public_key).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


def parse_datetime(value: Any) -> datetime | None:
    """Parse date or datetime values as timezone-aware UTC datetimes."""
    if value is None or isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        raise ValueError("datetime value must be an ISO date or datetime.")
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class SFTPUserKey(BaseModel):
    """Named SSH public key definition for schema v2 providers."""

    model_config = ConfigDict(extra="forbid")

    name: str
    public_key: str
    fingerprint: str | None = None
    comment: str | None = None
    disabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Validate a stable operator-facing key name."""
        if not KEY_NAME_RE.fullmatch(value):
            raise ValueError("Key name must match ^[a-z][a-z0-9._-]{0,63}$.")
        return value

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        """Validate and normalize the public key string."""
        return normalize_public_key(value)

    @field_validator("created_at", "updated_at", "expires_at", mode="before")
    @classmethod
    def validate_datetimes(cls, value: Any) -> datetime | None:
        """Normalize date and datetime fields."""
        return parse_datetime(value)

    @model_validator(mode="after")
    def derive_fingerprint(self) -> SFTPUserKey:
        """Derive or validate the stored key fingerprint."""
        expected = public_key_fingerprint(self.public_key)
        if self.fingerprint is None:
            self.fingerprint = expected
        elif self.fingerprint != expected:
            raise ValueError("Key fingerprint does not match public_key.")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        """Return whether this key should be written to authorized_keys."""
        if self.disabled:
            return False
        if self.expires_at is None:
            return True
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        return self.expires_at > reference.astimezone(UTC)


class SFTPUser(BaseModel):
    """SFTP user definition loaded from a provider."""

    model_config = ConfigDict(extra="forbid")

    username: str
    public_keys: list[str] = Field(default_factory=list)
    keys: list[SFTPUserKey] = Field(default_factory=list)
    password_hash: str | None = None
    uid: int | None = Field(default=None, ge=1000)
    gid: int | None = Field(default=None, ge=1000)
    upload_dir: str = "upload"
    comment: str | None = None
    disabled: bool = False

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """Validate an SFTP username.

        Parameters
        ----------
        value
            Username from provider data.

        Returns
        -------
        str
            Validated username.
        """
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("Username must match ^[a-z_][a-z0-9_-]{0,31}$.")
        return value

    @field_validator("public_keys")
    @classmethod
    def validate_public_keys(cls, keys: list[str]) -> list[str]:
        """Validate and normalize SSH public keys.

        Parameters
        ----------
        keys
            Public keys from provider data.

        Returns
        -------
        list[str]
            Stripped public keys.
        """
        return [normalize_public_key(key) for key in keys]

    @field_validator("upload_dir")
    @classmethod
    def validate_upload_dir(cls, value: str) -> str:
        """Validate the user's upload directory.

        Parameters
        ----------
        value
            Upload directory from provider data.

        Returns
        -------
        str
            Validated relative path.
        """
        validate_relative_safe_path(value, field_name="upload_dir")
        return value

    @model_validator(mode="after")
    def ensure_auth_present(self) -> SFTPUser:
        """Ensure the user has at least one usable authentication method.

        Returns
        -------
        SFTPUser
            Validated user model.
        """
        if self.keys and not self.public_keys:
            self.public_keys = [key.public_key for key in self.keys]
        self._validate_unique_keys()
        if not self.public_keys and not self.keys and not self.password_hash:
            raise ValueError("User requires at least one public key or a password_hash.")
        if self.password_hash and not self.password_hash.startswith(("$y$", "$6$", "!", "*")):
            raise ValueError("password_hash must be a system password hash, never plaintext.")
        return self

    def _validate_unique_keys(self) -> None:
        names = [key.name for key in self.keys]
        if len(names) != len(set(names)):
            raise ValueError("User contains duplicate key names.")
        fingerprints = [key.fingerprint for key in self.keys]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("User contains duplicate key fingerprints.")

    def key_objects(self) -> list[SFTPUserKey]:
        """Return named keys, deriving deterministic virtual keys for schema v1 data."""
        if self.keys:
            return list(self.keys)
        return [
            SFTPUserKey(
                name=deterministic_key_name(public_key),
                public_key=public_key,
                source="public_keys",
            )
            for public_key in self.public_keys
        ]

    def active_keys(self, now: datetime | None = None) -> list[SFTPUserKey]:
        """Return keys that should be active at runtime."""
        return [key for key in self.key_objects() if key.is_active(now)]

    def active_public_keys(self, now: datetime | None = None) -> list[str]:
        """Return active public key strings."""
        return [key.public_key for key in self.active_keys(now)]


class ProviderUsers(BaseModel):
    """Collection of provider users."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, ge=1)
    users: list[SFTPUser] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_unique_users(self) -> ProviderUsers:
        """Validate uniqueness constraints across provider users.

        Returns
        -------
        ProviderUsers
            Validated provider user collection.
        """
        names = [user.username for user in self.users]
        if len(names) != len(set(names)):
            raise ValueError("Provider contains duplicate usernames.")
        explicit_uids = [user.uid for user in self.users if user.uid is not None]
        if len(explicit_uids) != len(set(explicit_uids)):
            raise ValueError("Provider contains duplicate explicit UIDs.")
        return self
