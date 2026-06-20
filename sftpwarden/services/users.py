from __future__ import annotations

from dataclasses import dataclass

from sftpwarden.config import load_config
from sftpwarden.contexts import ContextEntry, resolve_context
from sftpwarden.providers import (
    SFTPUser,
    find_user,
    provider_from_config,
)
from sftpwarden.users.models import ProviderUsers


@dataclass(frozen=True)
class UserUpdateResult:
    user: SFTPUser
    runtime_changed: bool


class UserService:
    def __init__(self, *, context_name: str | None = None, config_path: str | None = None) -> None:
        self.entry = resolve_context(config_path=config_path, context_name=context_name)
        self.config = load_config(self.entry.config)
        self.provider = provider_from_config(self.entry.root, self.config)

    @property
    def context(self) -> ContextEntry:
        return self.entry

    def list_users(self) -> ProviderUsers:
        return self.provider.read()

    def show_user(self, username: str) -> SFTPUser:
        return find_user(self.list_users(), username)

    def add_user(
        self,
        *,
        username: str,
        public_keys: list[str] | None = None,
        password_hash: str | None = None,
        upload_dir: str = "upload",
        comment: str | None = None,
        uid: int | None = None,
        gid: int | None = None,
    ) -> SFTPUser:
        user = SFTPUser(
            username=username,
            public_keys=public_keys or [],
            password_hash=password_hash,
            upload_dir=upload_dir,
            comment=comment,
            uid=uid,
            gid=gid,
        )
        self.provider.upsert_user(user)
        return user

    def update_user(
        self,
        username: str,
        *,
        public_keys: list[str] | None = None,
        password_hash: str | None = None,
        upload_dir: str | None = None,
        comment: str | None = None,
        uid: int | None = None,
        gid: int | None = None,
        disabled: bool | None = None,
    ) -> UserUpdateResult:
        existing = find_user(self.provider.read(), username)
        runtime_changed = any(
            value is not None
            for value in (public_keys, password_hash, upload_dir, uid, gid, disabled)
        )
        updated = existing.model_copy(
            update={
                "public_keys": public_keys if public_keys is not None else existing.public_keys,
                "password_hash": password_hash
                if password_hash is not None
                else existing.password_hash,
                "upload_dir": upload_dir if upload_dir is not None else existing.upload_dir,
                "comment": comment if comment is not None else existing.comment,
                "uid": uid if uid is not None else existing.uid,
                "gid": gid if gid is not None else existing.gid,
                "disabled": disabled if disabled is not None else existing.disabled,
            }
        )
        self.provider.upsert_user(updated)
        return UserUpdateResult(user=updated, runtime_changed=runtime_changed)

    def remove_user(self, username: str) -> None:
        self.provider.remove_user(username)
