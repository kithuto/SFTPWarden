from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.users import service
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


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


class FileProvider(BaseProvider):
    missing_file_suggestion = "Create it or run `sftpwarden init`."

    def require_path(self) -> Path:
        if self.path is None:
            raise ProviderError(f"{self.provider_type.value.upper()} provider requires a path.")
        return self.path

    def ensure_exists(self) -> Path:
        path = self.require_path()
        if not path.exists():
            raise ProviderError(
                f"Provider file not found: {path}",
                suggestion=self.missing_file_suggestion,
            )
        return path

    def ensure_parent_dir(self) -> Path:
        path = self.require_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
