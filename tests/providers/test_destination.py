"""Recovery identity excludes credentials and rejects ambiguous implicit destinations."""

import sys
from types import SimpleNamespace

import pytest

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers.destination import destination_fingerprint
from sftpwarden.utils.errors import ProviderError


@pytest.mark.parametrize(
    "values",
    [
        {"host": "DB.example", "dbname": "users"},
        {"dbname": "users"},
        {"host": "DB.example"},
        {"host": "DB.example", "dbname": "users", "service": "implicit"},
    ],
)
def test_postgres_destination_requires_explicit_host_and_database(monkeypatch, values):
    monkeypatch.setitem(
        sys.modules, "psycopg.conninfo", SimpleNamespace(conninfo_to_dict=lambda dsn: values)
    )
    config = ProviderConfig(type="postgresql", dsn="postgresql://db.example/users")
    result = destination_fingerprint(config)
    if values == {"host": "DB.example", "dbname": "users"}:
        assert result is not None and len(result) == 64
        assert destination_fingerprint(config.model_copy(update={"table": "different"})) != result
    else:
        assert result is None


@pytest.mark.parametrize("dsn", ["mysql://db", "mysql:///database", "mysql://db:invalid/database"])
def test_incomplete_or_invalid_database_address_cannot_establish_isolation(dsn):
    config = ProviderConfig(type="mysql", dsn=dsn)
    if "invalid" in dsn:
        with pytest.raises(ProviderError, match="identified safely"):
            destination_fingerprint(config)
    else:
        assert destination_fingerprint(config) is None


@pytest.mark.parametrize(
    "provider,scheme",
    [
        (ProviderType.MYSQL, "mysql"),
        (ProviderType.MARIADB, "mariadb"),
        (ProviderType.MONGODB, "mongodb"),
    ],
)
def test_destination_is_independent_of_credentials_but_not_namespace(provider, scheme):
    first = ProviderConfig(
        type=provider, dsn=f"{scheme}://alice:synthetic%24secret@db.example/recovery"
    )
    second = first.model_copy(update={"dsn": f"{scheme}://bob:different@db.example/recovery"})
    assert destination_fingerprint(first) == destination_fingerprint(second)
    third = first.model_copy(update={"table": "separate", "collection": "separate"})
    assert destination_fingerprint(first) != destination_fingerprint(third)
    fourth = first.model_copy(update={"dsn": f"{scheme}://alice:synthetic@db.example/another"})
    assert destination_fingerprint(first) != destination_fingerprint(fourth)


def test_file_provider_never_creates_external_destination_identity():
    assert destination_fingerprint(ProviderConfig()) is None


def test_explicit_environment_is_resolved_once(monkeypatch):
    config = ProviderConfig(type="mysql", dsn="${RECOVERY_DSN}")
    monkeypatch.setenv("RECOVERY_DSN", "mysql://user:synthetic$literal@db/recovery")
    expected = config.model_copy(update={"dsn": "mysql://user:another@db/recovery"})
    assert destination_fingerprint(config) == destination_fingerprint(expected)
