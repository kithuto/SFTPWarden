from __future__ import annotations

import csv
import hashlib
import hmac
import re
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from sftpwarden.config import ProviderType, SFTPWardenConfig, provider_local_path
from sftpwarden.errors import ProviderError

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
    model_config = ConfigDict(extra="forbid")

    username: str
    public_keys: list[str] = Field(default_factory=list)
    password_hash: str | None = None
    uid: int | None = Field(default=None, ge=1000)
    gid: int | None = Field(default=None, ge=1000)
    upload_dir: str = "upload"
    disabled: bool = False

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("Username must match ^[a-z_][a-z0-9_-]{0,31}$.")
        return value

    @field_validator("public_keys")
    @classmethod
    def validate_public_keys(cls, keys: list[str]) -> list[str]:
        cleaned: list[str] = []
        for key in keys:
            stripped = key.strip()
            if not stripped.startswith(PUBLIC_KEY_PREFIXES):
                raise ValueError("Unsupported or invalid SSH public key.")
            cleaned.append(stripped)
        return cleaned

    @model_validator(mode="after")
    def ensure_auth_present(self) -> SFTPUser:
        if not self.public_keys and not self.password_hash:
            raise ValueError("User requires at least one public key or a password_hash.")
        if self.password_hash and not self.password_hash.startswith(("$y$", "$6$", "!", "*")):
            raise ValueError("password_hash must be a system password hash, never plaintext.")
        return self


class ProviderUsers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[SFTPUser] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_unique_users(self) -> ProviderUsers:
        names = [user.username for user in self.users]
        if len(names) != len(set(names)):
            raise ValueError("Provider contains duplicate usernames.")
        explicit_uids = [user.uid for user in self.users if user.uid is not None]
        if len(explicit_uids) != len(set(explicit_uids)):
            raise ValueError("Provider contains duplicate explicit UIDs.")
        return self


def empty_provider_text(provider_type: ProviderType) -> str:
    if provider_type == ProviderType.CSV:
        return "username,public_keys,password_hash,uid,gid,upload_dir,disabled\n"
    return yaml.safe_dump({"users": []}, sort_keys=False)


def load_users_from_project(project_root: str | Path, config: SFTPWardenConfig) -> ProviderUsers:
    provider_path = provider_local_path(project_root, config)
    return load_users(config.provider.type, provider_path)


def load_users(provider_type: ProviderType, path: str | Path) -> ProviderUsers:
    provider_path = Path(path)
    if provider_type == ProviderType.YAML:
        return load_yaml_users(provider_path)
    if provider_type == ProviderType.CSV:
        return load_csv_users(provider_path)
    raise ProviderError(
        f"{provider_type.value} provider reads are not configured in this local command yet.",
        suggestion="Use yaml/csv locally, or configure SQL runtime access inside the container.",
    )


def save_users(provider_type: ProviderType, path: str | Path, users: ProviderUsers) -> None:
    provider_path = Path(path)
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    if provider_type == ProviderType.YAML:
        data = {"users": [user.model_dump(mode="json", exclude_none=True) for user in users.users]}
        provider_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return
    if provider_type == ProviderType.CSV:
        with provider_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = [
                "username",
                "public_keys",
                "password_hash",
                "uid",
                "gid",
                "upload_dir",
                "disabled",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for user in users.users:
                row = user.model_dump(mode="json", exclude_none=True)
                row["public_keys"] = "\n".join(user.public_keys)
                writer.writerow(row)
        return
    raise ProviderError(
        f"{provider_type.value} provider mutations are not supported by the CLI.",
        suggestion="Use yaml/csv for direct mutations or add a SQL write strategy.",
    )


def load_yaml_users(path: Path) -> ProviderUsers:
    if not path.exists():
        raise ProviderError(
            f"Provider file not found: {path}", suggestion="Create it or run `sftpwarden init`."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"users": []}
    try:
        return ProviderUsers.model_validate(data)
    except ValidationError as exc:
        raise ProviderError(f"Invalid YAML provider file: {path}: {exc}") from exc


def load_csv_users(path: Path) -> ProviderUsers:
    if not path.exists():
        raise ProviderError(
            f"Provider file not found: {path}", suggestion="Create it or run `sftpwarden init`."
        )
    users: list[SFTPUser] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            public_keys = [
                key.strip() for key in (row.get("public_keys") or "").splitlines() if key.strip()
            ]
            users.append(
                SFTPUser(
                    username=row["username"],
                    public_keys=public_keys,
                    password_hash=row.get("password_hash") or None,
                    uid=int(row["uid"]) if row.get("uid") else None,
                    gid=int(row["gid"]) if row.get("gid") else None,
                    upload_dir=row.get("upload_dir") or "upload",
                    disabled=(row.get("disabled") or "").lower() in {"1", "true", "yes"},
                )
            )
    try:
        return ProviderUsers(users=users)
    except ValidationError as exc:
        raise ProviderError(f"Invalid CSV provider file: {path}: {exc}") from exc


def upsert_user(users: ProviderUsers, user: SFTPUser) -> ProviderUsers:
    next_users = [existing for existing in users.users if existing.username != user.username]
    next_users.append(user)
    next_users.sort(key=lambda item: item.username)
    return ProviderUsers(users=next_users)


def remove_user(users: ProviderUsers, username: str) -> ProviderUsers:
    next_users = [existing for existing in users.users if existing.username != username]
    if len(next_users) == len(users.users):
        raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")
    return ProviderUsers(users=next_users)


def find_user(users: ProviderUsers, username: str) -> SFTPUser:
    for user in users.users:
        if hmac.compare_digest(user.username, username):
            return user
    raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")


def users_fingerprint(users: ProviderUsers) -> str:
    canonical = yaml.safe_dump(
        {"users": [user.model_dump(mode="json", exclude_none=True) for user in users.users]},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
