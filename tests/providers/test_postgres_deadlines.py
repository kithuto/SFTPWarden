from __future__ import annotations

import sys

import pytest

from sftpwarden.providers import postgres_transport as transport
from sftpwarden.utils.errors import ProviderError

from .test_sql_providers import FakeConnection, FakeCursor, install_fake_psycopg


@pytest.mark.parametrize("timeout", ["0", "-1", "31", "1.5", "secret-sentinel"])
def test_postgres_rejects_unbounded_connection_timeout(timeout, monkeypatch):
    connection = FakeConnection(FakeCursor())
    install_fake_psycopg(monkeypatch, connection)
    with pytest.raises(ProviderError) as caught:
        transport.connect_postgres(
            sys.modules["psycopg"], f"postgresql://db/test?connect_timeout={timeout}"
        )
    assert "secret-sentinel" not in str(caught.value)
    assert not hasattr(connection, "dsn")


def test_postgres_keeps_requested_stricter_connect_timeout(monkeypatch):
    connection = FakeConnection(FakeCursor())
    install_fake_psycopg(monkeypatch, connection)
    transport.connect_postgres(
        sys.modules["psycopg"], "postgresql://db/test?connect_timeout=2&sslmode=verify-full"
    )
    assert connection.kwargs["connect_timeout"] == 2
    assert "sslmode=verify-full" in connection.dsn


def test_idle_socket_deadline_closes_connection_and_generator(monkeypatch):
    connection = FakeConnection(FakeCursor())
    install_fake_psycopg(monkeypatch, connection)
    transport.connect_postgres(sys.modules["psycopg"], "postgresql://db/test")
    clock = iter([0, 0, 21])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(clock))
    finalized = []

    def wait_forever():
        try:
            while True:
                yield 1
        finally:
            finalized.append(True)

    with pytest.raises(TimeoutError):
        connection.wait(wait_forever())
    assert connection.closed
    assert finalized == [True]


def test_completed_socket_operation_preserves_result_and_connection(monkeypatch):
    monkeypatch.setattr(transport.time, "monotonic", lambda: 0)
    closed = []

    def ready():
        result = yield 1
        return result + 1

    generator = transport.deadline_generator(
        ready(), deadline=20, close=lambda: closed.append(True)
    )
    assert next(generator) == 1
    with pytest.raises(StopIteration) as finished:
        generator.send(41)
    assert finished.value.value == 42
    assert closed == []
