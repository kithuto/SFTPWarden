"""Observational schema discovery for configured providers."""

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.errors import ProviderError

SQL_PROVIDERS = {
    ProviderType.SQLITE,
    ProviderType.MYSQL,
    ProviderType.MARIADB,
    ProviderType.POSTGRESQL,
}


def read_schema_snapshot(provider: BaseProvider) -> ProviderUsers:
    """Discover SQL v2 storage before a v1 projection can hide named-key policy."""
    if provider.config.type in SQL_PROVIDERS:
        newer = (
            provider
            if provider.config.user_schema == 2
            else type(provider)(
                provider.config.model_copy(update={"user_schema": 2}),
                path=provider.path,
            )
        )
        if newer.table_exists():  # type: ignore[attr-defined]
            return newer.read()
        if provider.config.user_schema == 2:
            older = type(provider)(
                provider.config.model_copy(update={"user_schema": 1}), path=provider.path
            )
            if older.table_exists():  # type: ignore[attr-defined]
                return older.read()
    return provider.read()


def require_configured_schema(provider: BaseProvider) -> ProviderUsers:
    """Read without writes, rejecting schema drift before users can be applied."""
    users = read_schema_snapshot(provider)
    if users.schema_version != provider.config.user_schema:
        raise ProviderError(
            "Provider data schema differs from provider.user_schema.",
            suggestion=(
                "Run `sftpwarden provider schema migrate --to 2` and deploy the matching config."
            ),
        )
    return users
