from __future__ import annotations

import subprocess

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.remote.checks import ssh_base_command, verify_remote_runtime_requirements


def test_ssh_base_command_uses_default_identity() -> None:
    remote = RemoteEndpoint(
        host="example.com",
        user="deploy",
        remote_root="/opt/sftpwarden",
        remote_config="/opt/sftpwarden/sftpwarden.yaml",
        ssh_key="default",
    )

    command = ssh_base_command(remote)

    assert "-i" not in command
    assert "deploy@example.com" in command


def test_verify_remote_runtime_requirements_runs_expected_checks(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    remote = RemoteEndpoint(
        host="example.com",
        user="deploy",
        remote_root="/opt/sftpwarden",
        remote_config="/opt/sftpwarden/sftpwarden.yaml",
    )

    verify_remote_runtime_requirements(remote)

    assert calls[0][-1] == "true"
    assert calls[1][-1] == "docker compose version"
