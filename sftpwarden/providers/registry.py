from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from sftpwarden.config import ProviderConfig, ProviderType, SFTPWardenConfig, provider_local_path
from sftpwarden.providers.base import BaseProvider
from sftpwarden.utils.errors import ProviderError

_ProviderT = TypeVar("_ProviderT", bound=BaseProvider)
ProviderClass = type[BaseProvider]
_PROVIDERS: dict[ProviderType, ProviderClass] = {}


def register_provider(provider_class: type[_ProviderT]) -> type[_ProviderT]:
    """Register a provider class.

    Parameters
    ----------
    provider_class
        Provider class to register.

    Returns
    -------
    type[_ProviderT]
        The same class, enabling decorator usage.
    """
    _PROVIDERS[provider_class.provider_type] = provider_class
    return provider_class


def provider_class(provider_type: ProviderType | str) -> ProviderClass:
    """Return the registered class for a provider type.

    Parameters
    ----------
    provider_type
        Provider type or string value.

    Returns
    -------
    ProviderClass
        Registered provider class.

    Raises
    ------
    ProviderError
        Raised when the provider is not registered.
    """
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
    collection: str = "sftp_users",
) -> BaseProvider:
    """Build a provider instance from explicit provider settings.

    Parameters
    ----------
    provider_type
        Provider type or string value.
    path
        Optional local provider file path.
    dsn
        Optional SQL DSN.
    query
        Optional SQL read query.
    table
        SQL table name.
    collection
        MongoDB collection name.

    Returns
    -------
    BaseProvider
        Provider instance.
    """
    normalized = ProviderType(provider_type)
    provider_config = ProviderConfig(
        type=normalized,
        dsn=dsn,
        query=query,
        table=table,
        collection=collection,
    )
    provider_path = Path(path) if path is not None else None
    return provider_class(normalized)(provider_config, path=provider_path)


def provider_from_config(project_root: str | Path, config: SFTPWardenConfig) -> BaseProvider:
    """Build a provider for a project config.

    Parameters
    ----------
    project_root
        Local project root.
    config
        Loaded project configuration.

    Returns
    -------
    BaseProvider
        Provider instance.
    """
    return provider_class(config.provider.type)(
        config.provider,
        path=provider_local_path(project_root, config),
    )


def registered_providers() -> dict[ProviderType, ProviderClass]:
    """Return registered provider classes.

    Returns
    -------
    dict[ProviderType, ProviderClass]
        Copy of the provider registry.
    """
    return dict(_PROVIDERS)
