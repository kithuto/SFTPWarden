"""Deterministic contracts for disposable release-test infrastructure helpers."""

from types import SimpleNamespace

import pytest

from . import conftest as harness

pytestmark = pytest.mark.release_validation


@pytest.mark.parametrize("failures", [0, 1, 3])
def test_gateway_retries_only_port_collisions_and_removes_failed_owned_containers(
    monkeypatch, failures
):
    commands = []
    removed = []
    ports = iter([22001, 22002, 22003])
    monkeypatch.setattr(harness, "free_tcp_port", lambda: next(ports))
    monkeypatch.setattr(harness, "remove_docker_container", removed.append)

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] is False
        return SimpleNamespace(
            returncode=int(len(commands) <= failures), stderr="address already in use"
        )

    monkeypatch.setattr(harness, "run_external", run)
    if failures == 3:
        with pytest.raises(pytest.fail.Exception, match="three attempts"):
            harness.start_stable_ssh_gateway("owned-gateway", ["image"])
    else:
        assert harness.start_stable_ssh_gateway("owned-gateway", ["image"]) == 22001 + failures
    assert removed == ["owned-gateway"] * failures
    assert len(commands) == min(failures + 1, 3)
    for index, command in enumerate(commands):
        assert command[command.index("--publish") + 1] == f"127.0.0.1:{22001 + index}:22"


def test_gateway_does_not_retry_or_remove_containers_after_unrelated_errors(monkeypatch):
    monkeypatch.setattr(harness, "free_tcp_port", lambda: 22001)
    monkeypatch.setattr(
        harness,
        "run_external",
        lambda *a, **kw: SimpleNamespace(
            returncode=1, stderr="Container name belongs to another run"
        ),
    )
    monkeypatch.setattr(
        harness, "remove_docker_container", lambda _: pytest.fail("Unrelated container removed")
    )
    with pytest.raises(pytest.fail.Exception, match="belongs to another run"):
        harness.start_stable_ssh_gateway("conflicting-name", ["image"])
