from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sftpwarden.utils.validation import validate_relative_safe_path

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PUBLIC_KEY_PREFIXES = (
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "sk-ecdsa-sha2-nistp256@openssh.com ",
    "sk-ssh-ed25519@openssh.com ",
    "ssh-ed25519 ",
    "ssh-rsa ",
)


class SFTPUser(BaseModel):
    """SFTP user definition loaded from a provider."""

    model_config = ConfigDict(extra="forbid")

    username: str
    public_keys: list[str] = Field(default_factory=list)
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
        cleaned: list[str] = []
        for key in keys:
            stripped = key.strip()
            if not stripped.startswith(PUBLIC_KEY_PREFIXES):
                raise ValueError("Unsupported or invalid SSH public key.")
            cleaned.append(stripped)
        return cleaned

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
        if not self.public_keys and not self.password_hash:
            raise ValueError("User requires at least one public key or a password_hash.")
        if self.password_hash and not self.password_hash.startswith(("$y$", "$6$", "!", "*")):
            raise ValueError("password_hash must be a system password hash, never plaintext.")
        return self


class ProviderUsers(BaseModel):
    """Collection of provider users."""

    model_config = ConfigDict(extra="forbid")

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
