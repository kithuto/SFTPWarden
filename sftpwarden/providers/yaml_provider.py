from __future__ import annotations

import yaml
from pydantic import ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import FileProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers
from sftpwarden.utils.errors import ProviderError


@register_provider
class YAMLProvider(FileProvider):
    provider_type = ProviderType.YAML

    @classmethod
    def empty_text(cls) -> str:
        return yaml.safe_dump({"users": []}, sort_keys=False)

    def read(self) -> ProviderUsers:
        path = self.ensure_exists()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"users": []}
        try:
            return ProviderUsers.model_validate(data)
        except ValidationError as exc:
            raise ProviderError(f"Invalid YAML provider file: {path}: {exc}") from exc

    def write(self, users: ProviderUsers) -> None:
        path = self.ensure_parent_dir()
        data = {"users": [user.model_dump(mode="json", exclude_none=True) for user in users.users]}
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
