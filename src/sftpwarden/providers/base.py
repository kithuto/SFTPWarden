from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.users import service
from sftpwarden.users.models import ProviderUsers, SFTPUser


class BaseProvider(ABC):
    provider_type: ProviderType
    mutable: bool = True

    def __init__(self, config: ProviderConfig, *, path: Path | None = None) -> None:
        self.config = config
        self.path = path

    @classmethod
    @abstractmethod
    def empty_text(cls) -> str:
        raise NotImplementedError

    @abstractmethod
    def read(self) -> ProviderUsers:
        raise NotImplementedError

    @abstractmethod
    def write(self, users: ProviderUsers) -> None:
        raise NotImplementedError

    def upsert_user(self, user: SFTPUser) -> None:
        self.write(service.upsert_user(self.read(), user))

    def remove_user(self, username: str) -> None:
        self.write(service.remove_user(self.read(), username))
