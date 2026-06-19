from __future__ import annotations

from pathlib import Path

from sftpwarden.config import ProviderConfig, ProviderType, SFTPWardenConfig, provider_local_path
from sftpwarden.utils.errors import ProviderError
from sftpwarden.providers.base import BaseProvider

ProviderClass = type[BaseProvider]
_PROVIDERS: dict[ProviderType, ProviderClass] = {}


def register_provider(provider_class: ProviderClass) -> ProviderClass:
    _PROVIDERS[provider_class.provider_type] = provider_class
    return provider_class


def provider_class(provider_type: ProviderType | str) -> ProviderClass:
    normalized = ProviderType(provider_type)
    try:
        return _PROVIDERS[normalized]
    except KeyError as exc:
        raise ProviderError(f"Provider is not registered: {normalized.value}") from exc


def build_provider(
    provider_type: ProviderType | str,
    *,
    path: str | Path | None = None,
    dsn: str | None = None,
    query: str | None = None,
    table: str = "sftp_users",
) -> BaseProvider:
    normalized = ProviderType(provider_type)
    provider_config = ProviderConfig(type=normalized, dsn=dsn, query=query, table=table)
    provider_path = Path(path) if path is not None else None
    return provider_class(normalized)(provider_config, path=provider_path)


def provider_from_config(project_root: str | Path, config: SFTPWardenConfig) -> BaseProvider:
    return provider_class(config.provider.type)(
        config.provider,
        path=provider_local_path(project_root, config),
    )


def registered_providers() -> dict[ProviderType, ProviderClass]:
    return dict(_PROVIDERS)
