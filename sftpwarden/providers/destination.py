"""Non-secret namespace fingerprints for explicit external-provider recovery selection."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qsl, unquote, urlsplit

from sftpwarden.config import FILE_PROVIDER_TYPES, ProviderConfig, ProviderType
from sftpwarden.security.environment import resolve_provider_dsn
from sftpwarden.utils.errors import ProviderError


def destination_fingerprint(config: ProviderConfig) -> str | None:
    """Identify explicit endpoint/database/table inputs without credential-derived values.

    This detects reuse of the same configured namespace. It is not proof that two
    DNS aliases resolve to different servers; the selected target must also be empty
    and independently protected before any checked replacement.
    """
    if config.type in FILE_PROVIDER_TYPES:
        return None
    dsn = resolve_provider_dsn(config.dsn or "")
    try:
        if config.type == ProviderType.POSTGRESQL:
            from psycopg.conninfo import conninfo_to_dict

            values = conninfo_to_dict(dsn)
            # Implicit libpq environment/service destinations cannot establish isolation.
            if not values.get("dbname") or not values.get("host") or values.get("service"):
                return None
            identity = [
                "postgresql",
                str(values["host"]).lower(),
                values.get("port", "5432"),
                values["dbname"],
                config.table,
            ]
        else:
            parsed = urlsplit(dsn)
            database = unquote(parsed.path.lstrip("/"))
            endpoint = parsed.netloc.rsplit("@", 1)[-1].lower()
            if not database or not endpoint:
                return None
            options = dict(parse_qsl(parsed.query))
            if config.type == ProviderType.MONGODB:
                identity = [
                    parsed.scheme,
                    endpoint,
                    database,
                    config.collection,
                    options.get("replicaSet", ""),
                ]
            else:
                identity = [
                    "mysql",
                    parsed.hostname,
                    parsed.port or 3306,
                    options.get("unix_socket", ""),
                    database,
                    config.table,
                ]
        return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    except (ValueError, TypeError):
        raise ProviderError(
            "External provider destination could not be identified safely."
        ) from None
