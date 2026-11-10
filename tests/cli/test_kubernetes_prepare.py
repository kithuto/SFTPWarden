"""The explicit preparation command is read-only in preview and does not replace Pods."""

import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from sftpwarden.cli import app
from sftpwarden.cli_commands import kubernetes
from sftpwarden.services import kubernetes_identity, kubernetes_lock
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
def test_prepare_owns_mutation_window_and_reports_unverified_preview(
    local_project_factory, monkeypatch, dry_run, json_output
):
    _, entry = local_project_factory()
    events = []
    monkeypatch.setattr(kubernetes_identity, "pin_context", lambda config: config)
    monkeypatch.setattr(
        kubernetes_identity, "prepare_identity", lambda config: events.append("identity")
    )
    monkeypatch.setattr(
        kubernetes_identity, "prepare_provider_secret", lambda config: events.append("credentials")
    )

    @contextmanager
    def lock(config, purpose):
        assert purpose == "preparation"
        events.append("locked")
        yield
        events.append("unlocked")

    monkeypatch.setattr(kubernetes_lock, "operation_lock", lock)
    result = CliRunner().invoke(
        app,
        [
            "kube",
            "prepare",
            "--context",
            entry.name,
            *(["--dry-run"] if dry_run else []),
            *(["--json"] if json_output else []),
        ],
    )
    assert result.exit_code == 0, result.output
    assert events == ([] if dry_run else ["locked", "identity", "credentials", "unlocked"])
    if json_output:
        assert json.loads(result.output)["status"] == (
            "live_prerequisites_unverified" if dry_run else "prepared"
        )
    else:
        assert ("not been checked" if dry_run else "not been replaced") in " ".join(
            result.output.split()
        )


@pytest.mark.parametrize("missing_config", [False, True])
def test_prepare_reports_failed_preconditions_without_success(
    local_project_factory, monkeypatch, missing_config
):
    _, entry = local_project_factory()
    if missing_config:
        entry.config = ""
    monkeypatch.setattr(kubernetes, "resolve_context", lambda **kw: entry)

    def fail(config):
        raise RuntimeError("cluster unavailable")

    monkeypatch.setattr(kubernetes_identity, "pin_context", fail)
    result = CliRunner().invoke(app, ["kube", "prepare"])
    assert result.exit_code != 0
    assert "prepared;" not in result.output
