from __future__ import annotations

import yaml
from pydantic import ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers
from sftpwarden.utils.errors import ProviderError


@register_provider
class YAMLProvider(BaseProvider):
    provider_type = ProviderType.YAML

    @classmethod
    def empty_text(cls) -> str:
        return yaml.safe_dump({"users": []}, sort_keys=False)

    def read(self) -> ProviderUsers:
        if self.path is None:
            raise ProviderError("YAML provider requires a path.")
        if not self.path.exists():
            raise ProviderError(
                f"Provider file not found: {self.path}",
                suggestion="Create it or run `sftpwarden init`.",
            )
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {"users": []}
        try:
            return ProviderUsers.model_validate(data)
        except ValidationError as exc:
            raise ProviderError(f"Invalid YAML provider file: {self.path}: {exc}") from exc

    def write(self, users: ProviderUsers) -> None:
        if self.path is None:
            raise ProviderError("YAML provider requires a path.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"users": [user.model_dump(mode="json", exclude_none=True) for user in users.users]}
        self.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
