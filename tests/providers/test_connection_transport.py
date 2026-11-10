from __future__ import annotations

import sys
from urllib.parse import quote

import pytest

from sftpwarden.config import ProviderConfig, ProviderType, default_project_config
from sftpwarden.providers import MongoDBProvider, MySQLProvider, PostgreSQLProvider
from sftpwarden.providers.mysql_provider import mariadb_connect_kwargs, mysql_connect_kwargs
from sftpwarden.render.compose import compose_model, compose_text
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ConfigError, ProviderError

from .test_sql_providers import (
    FakeConnection,
    FakeCursor,
    install_fake_psycopg,
    install_fake_pymysql,
)


@pytest.mark.parametrize("reference", ["${DB_URL}", "$DB_URL", "%DB_URL%"])
@pytest.mark.parametrize("kind", ["mysql", "postgresql", "mongodb"])
def test_provider_expands_whole_reference_once(
    reference, kind, monkeypatch, install_fake_pymongo
) -> None:
    literal = f"{kind}://user:literal$PASSWORD%25@localhost/test"
    monkeypatch.setenv("DB_URL", literal)
    monkeypatch.setenv("PASSWORD", "must-not-replace")
    config = ProviderConfig(type=kind, dsn=reference)
    if kind == "mongodb":
        databases = install_fake_pymongo()
        MongoDBProvider(config).create_table()
        MongoDBProvider(config).read()
        assert (literal, "test") in databases
    else:
        connection = FakeConnection(FakeCursor())
        if kind == "mysql":
            install_fake_pymysql(monkeypatch, connection)
            MySQLProvider(config).read()
            assert connection.kwargs["password"] == "literal$PASSWORD%"
        else:
            install_fake_psycopg(monkeypatch, connection)
            PostgreSQLProvider(config).read()
            assert connection.dsn == literal


@pytest.mark.parametrize("kind", ["mysql", "postgresql", "mongodb"])
def test_literal_dollar_credentials_are_not_expanded(
    kind, monkeypatch, install_fake_pymongo
) -> None:
    dsn = f"{kind}://user:$PASSWORD@localhost/test"
    monkeypatch.setenv("PASSWORD", "wrong-password")
    config = ProviderConfig(type=kind, dsn=dsn)
    if kind == "mongodb":
        databases = install_fake_pymongo()
        MongoDBProvider(config).create_table()
        MongoDBProvider(config).read()
        assert (dsn, "test") in databases
    else:
        connection = FakeConnection(FakeCursor())
        if kind == "mysql":
            install_fake_pymysql(monkeypatch, connection)
            MySQLProvider(config).read()
            assert connection.kwargs["password"] == "$PASSWORD"
        else:
            install_fake_psycopg(monkeypatch, connection)
            PostgreSQLProvider(config).read()
            assert connection.dsn == dsn


@pytest.mark.parametrize(
    "parser,scheme", [(mysql_connect_kwargs, "mysql"), (mariadb_connect_kwargs, "mariadb")]
)
def test_mysql_tls_options_are_enforced_and_credentials_decoded(parser, scheme) -> None:
    kwargs = parser(
        f"{scheme}://u:{quote('synthetic$pass@;&%', safe='')}@db/test%20db"
        "?ssl_ca=%2Fprivate%2Fca.pem&ssl_cert=%2Fprivate%2Fclient.pem"
        "&ssl_key=%2Fprivate%2Fclient.key&connect_timeout=2&read_timeout=3&write_timeout=4"
        "&charset=utf8mb4"
    )
    assert kwargs["password"] == "synthetic$pass@;&%"
    assert kwargs["database"] == "test db"
    assert kwargs["ssl_ca"] == "/private/ca.pem"
    assert kwargs["ssl_cert"] == "/private/client.pem"
    assert kwargs["ssl_key"] == "/private/client.key"
    assert kwargs["ssl_verify_cert"] is True
    assert kwargs["ssl_verify_identity"] is True
    assert [kwargs[name] for name in ("connect_timeout", "read_timeout", "write_timeout")] == [
        2,
        3,
        4,
    ]
    assert kwargs["charset"] == "utf8mb4"


@pytest.mark.parametrize(
    "option",
    [
        "ssl_mode=REQUIRED",
        "ssl_ca=",
        "ssl_ca=a&ssl_ca=b",
        "read_timeout=0",
        "read_timeout=31",
        "read_timeout=1.5",
        "read_timeout=secret-sentinel",
        "ssl_verify_cert=false",
        "ssl_verify_identity=0",
        "ssl_disabled=unknown",
        "ssl_ca=ca.pem&ssl_disabled=true",
        "ssl_key=key.pem",
        "ssl_ca",
        "ssl_verify_cert=secret-sentinel",
    ],
)
def test_invalid_mysql_transport_options_fail_without_disclosing_values(option) -> None:
    with pytest.raises(ProviderError) as error:
        mysql_connect_kwargs(f"mysql://user:secret-sentinel@localhost/test?{option}")
    assert "secret-sentinel" not in str(error.value)


@pytest.mark.parametrize("suffix", [":secret-sentinel/test", "/test#secret-sentinel"])
def test_invalid_mysql_url_does_not_echo_credentials(suffix) -> None:
    with pytest.raises(ProviderError) as error:
        mysql_connect_kwargs(f"mysql://u:secret-sentinel@localhost{suffix}")
    assert "secret-sentinel" not in str(error.value)


def test_mysql_defaults_are_finite_and_explicit_tls_uses_verification() -> None:
    kwargs = mysql_connect_kwargs("mysql://localhost/test")
    assert [kwargs[name] for name in ("connect_timeout", "read_timeout", "write_timeout")] == [
        5,
        20,
        20,
    ]
    kwargs = mysql_connect_kwargs("mysql://localhost/test?ssl_disabled=false")
    assert kwargs["ssl_verify_cert"] is kwargs["ssl_verify_identity"] is True


@pytest.mark.parametrize(
    "reference", ["${DB_URL}", "$DB_URL", "%DB_URL%", "mysql://${DB_USER}:${DB_PASS}@db/test"]
)
def test_compose_delivers_only_required_references_without_resolving(
    reference, monkeypatch
) -> None:
    config = default_project_config("transport")
    config.provider = ProviderConfig(type=ProviderType.MYSQL, dsn=reference)
    monkeypatch.setenv("DB_URL", "mysql://u:secret-sentinel@db/test")
    monkeypatch.setenv("UNRELATED_SECRET", "unrelated-sentinel")
    environment = compose_model(config)["services"]["sftpwarden"]["environment"]
    names = {"DB_USER", "DB_PASS"} if "DB_USER" in reference else {"DB_URL"}
    assert set(environment) == names | {"SFTPWARDEN_CONFIG", "SFTPWARDEN_HOST_KEYS_DIR"}
    for name in names:
        assert environment[name].startswith("${" + name + ":?")
    rendered = compose_text(config)
    assert "secret-sentinel" not in rendered
    assert "UNRELATED_SECRET" not in rendered


def test_compose_rejects_dsn_reference_to_reserved_runtime_variable() -> None:
    config = default_project_config("transport")
    config.provider = ProviderConfig(type=ProviderType.MYSQL, dsn="${SFTPWARDEN_CONFIG}")
    with pytest.raises(ConfigError, match="reserved"):
        compose_model(config)


@pytest.mark.parametrize(
    "method",
    ["read", "upsert_user", "remove_user", "table_exists", "create_table", "write", "mutate_user"],
)
@pytest.mark.parametrize("failure", [False, True])
def test_mongodb_closes_each_client_after_success_or_failure(
    method, failure, monkeypatch, install_fake_pymongo, user_factory
) -> None:
    databases = install_fake_pymongo()
    factory = sys.modules["pymongo"].MongoClient
    clients = []

    def create_client(dsn, **kwargs):
        client = factory(dsn, **kwargs)
        clients.append(client)
        client.closed = False

        def close():
            client.closed = True

        client.close = close
        return client

    monkeypatch.setattr(sys.modules["pymongo"], "MongoClient", create_client)
    provider = MongoDBProvider(ProviderConfig(type="mongodb", dsn="mongodb://localhost/test"))
    user: SFTPUser = user_factory("alice")
    provider.upsert_user(user)
    database = databases[("mongodb://localhost/test", "test")]
    collection = database[provider.config.collection]
    if failure:

        def fail(*args, **kwargs):
            raise ValueError("synthetic database failure")

        for name in ("find", "replace_one", "delete_one", "find_one"):
            monkeypatch.setattr(collection, name, fail)
        monkeypatch.setattr(database, "list_collection_names", fail)
    clients.clear()
    args = {
        "upsert_user": (user,),
        "remove_user": ("alice",),
        "write": (ProviderUsers(users=[user]),),
        "mutate_user": (ProviderUsers(users=[user]), "alice", user),
    }.get(method, ())
    if failure:
        with pytest.raises((ValueError, ProviderError)):
            getattr(provider, method)(*args)
    else:
        getattr(provider, method)(*args)
    assert len(clients) == 1
    assert all(client.closed for client in clients)


@pytest.mark.parametrize(
    "query",
    [
        "connectTimeoutMS=0",
        "serverSelectionTimeoutMS=5001",
        "socketTimeoutMS=-1",
        "timeoutMS=25001",
        "timeoutMS=secret-sentinel",
        "timeoutMS=2&TIMEOUTMS=3",
    ],
)
def test_mongodb_rejects_unbounded_or_ambiguous_timeouts(query, install_fake_pymongo) -> None:
    databases = install_fake_pymongo()
    provider = MongoDBProvider(
        ProviderConfig(type="mongodb", dsn=f"mongodb://localhost/test?{query}")
    )
    with pytest.raises(ProviderError) as error:
        provider.read()
    assert "secret-sentinel" not in str(error.value)
    assert not databases


def test_mongodb_keeps_stricter_uri_timeout_and_bounds_remaining_operations(
    install_fake_pymongo,
) -> None:
    databases = install_fake_pymongo()
    dsn = "mongodb://localhost/test?socketTimeoutMS=100&TIMEOUTMS=200"
    MongoDBProvider(ProviderConfig(type="mongodb", dsn=dsn)).create_table()
    MongoDBProvider(ProviderConfig(type="mongodb", dsn=dsn)).read()
    assert databases[(dsn, "test")].client.options == {
        "connectTimeoutMS": 5000,
        "serverSelectionTimeoutMS": 5000,
        "socketTimeoutMS": 100,
        "timeoutMS": 200,
    }
