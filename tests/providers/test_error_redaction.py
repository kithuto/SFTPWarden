"""Provider error boundaries must not expose driver credentials or nested causes."""

import ssl
import sys
import traceback
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from sftpwarden.cli import app
from sftpwarden.config import ProviderConfig, default_project_config, write_config
from sftpwarden.providers import provider_for_config
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError

from .test_sql_providers import (
    FakeConnection,
    FakeCursor,
    install_fake_psycopg,
    install_fake_pymysql,
)

SECRET = "synthetic-secret$;&%"
METHODS = [
    "read",
    "write",
    "upsert_user",
    "remove_user",
    "table_exists",
    "create_table",
    "mutate_user",
    "replace_if_unchanged",
]


def invoke(provider, method):
    users = ProviderUsers(schema_version=1, users=[SFTPUser(username="alice")])
    args = {
        "write": (users,),
        "upsert_user": (users.users[0],),
        "remove_user": ("alice",),
        "mutate_user": (users, "alice", users.users[0]),
        "replace_if_unchanged": (users, users),
        "ensure_schema_storage": (2,),
    }.get(method, ())
    return getattr(provider, method)(*args)


@pytest.mark.parametrize("kind", ["mysql", "mariadb", "postgresql", "mongodb"])
@pytest.mark.parametrize("method", METHODS)
def test_driver_connection_failures_hide_raw_encoded_and_nested_credentials(
    kind,
    method,
    monkeypatch,
    install_fake_pymongo,
) -> None:
    def fail(*args, **kwargs):
        try:
            raise ValueError(SECRET)
        except ValueError as cause:
            raise RuntimeError(f"{kind}://user:{quote(SECRET, safe='')}@db/test") from cause

    if kind == "mongodb":
        install_fake_pymongo()
        monkeypatch.setattr(sys.modules["pymongo"], "MongoClient", fail)
    else:
        install = install_fake_psycopg if kind == "postgresql" else install_fake_pymysql
        install(monkeypatch, FakeConnection(FakeCursor()))
        module = "psycopg" if kind == "postgresql" else "pymysql"
        monkeypatch.setattr(sys.modules[module], "connect", fail)
    provider = provider_for_config(ProviderConfig(type=kind, dsn=f"{kind}://localhost/test"))
    with pytest.raises(ProviderError) as failure:
        invoke(provider, method)
    displayed = "".join(traceback.format_exception(failure.value))
    assert SECRET not in displayed
    assert quote(SECRET, safe="") not in displayed
    assert failure.value.__cause__ is None


@pytest.mark.parametrize("postgres", [False, True])
@pytest.mark.parametrize("method", [*METHODS, "ensure_schema_storage"])
def test_sql_query_failures_are_redacted_after_cleanup(postgres, method, monkeypatch) -> None:
    error = RuntimeError(SECRET)
    cursor = FakeCursor(execute_error=error, executemany_error=error)
    connection = FakeConnection(cursor)
    (install_fake_psycopg if postgres else install_fake_pymysql)(monkeypatch, connection)
    kind = "postgresql" if postgres else "mysql"
    provider = provider_for_config(ProviderConfig(type=kind, dsn=f"{kind}://localhost/test"))
    with pytest.raises(ProviderError) as failure:
        invoke(provider, method)
    assert SECRET not in "".join(traceback.format_exception(failure.value))
    if not postgres:
        assert connection.closed
        assert not connection.committed
        if method not in {"read", "table_exists"}:
            assert connection.rolled_back


@pytest.mark.parametrize(
    "error,description",
    [
        (ssl.SSLCertVerificationError(SECRET), "TLS certificate verification failed"),
        (TimeoutError(SECRET), "timed out"),
    ],
)
def test_nested_transport_failure_retains_safe_diagnostic(error, description, monkeypatch) -> None:
    install_fake_pymysql(monkeypatch, FakeConnection(FakeCursor()))

    def fail(*args, **kwargs):
        raise RuntimeError(SECRET) from error

    monkeypatch.setattr(sys.modules["pymysql"], "connect", fail)
    provider = provider_for_config(ProviderConfig(type="mysql", dsn="mysql://localhost/test"))
    with pytest.raises(ProviderError, match=description) as failure:
        provider.read()
    assert SECRET not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_does_not_expose_provider_diagnostics_even_with_debug(
    tmp_path,
    monkeypatch,
    debug,
    json_output,
) -> None:
    install_fake_pymysql(monkeypatch, FakeConnection(FakeCursor()))
    dsn = f"mysql://user:{quote(SECRET, safe='')}@localhost/test"

    def fail(*args, **kwargs):
        raise ValueError(dsn + " " + SECRET)

    monkeypatch.setattr(sys.modules["pymysql"], "connect", fail)
    monkeypatch.setenv("SFTPWARDEN_DEBUG", "1" if debug else "0")
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "private-home"))
    config = default_project_config("redaction")
    config.provider = ProviderConfig(type="mysql", dsn=dsn)
    path = tmp_path / "sftpwarden.yaml"
    write_config(path, config)
    result = CliRunner().invoke(
        app,
        ["users", "--config", str(path), *(["--json"] if json_output else [])],
    )
    assert result.exit_code == 1, result.output
    assert "Database provider operation failed" in result.output
    assert SECRET not in result.output
    assert quote(SECRET, safe="") not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("domain", [False, True])
def test_error_chains_cannot_reveal_secrets_or_loop_forever(domain, monkeypatch) -> None:
    install_fake_pymysql(monkeypatch, FakeConnection(FakeCursor()))
    nested = RuntimeError(SECRET)
    nested.__cause__ = nested
    error = ProviderError("Controlled error.", suggestion="Controlled fix.") if domain else nested
    if domain:
        error.__cause__ = nested

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(sys.modules["pymysql"], "connect", fail)
    provider = provider_for_config(ProviderConfig(type="mysql", dsn="mysql://localhost/test"))
    with pytest.raises(ProviderError) as failure:
        provider.read()
    assert SECRET not in "".join(traceback.format_exception(failure.value))
    if domain:
        assert failure.value.message == "Controlled error."
        assert failure.value.suggestion == "Controlled fix."
