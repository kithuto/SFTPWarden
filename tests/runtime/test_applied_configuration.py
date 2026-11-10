import os
import subprocess
from pathlib import Path

import pytest

from sftpwarden.config import default_project_config, runtime_config_fingerprints, write_config
from sftpwarden.runtime import configuration
from sftpwarden.utils.errors import RuntimeError


def test_applied_config_survives_desired_edits_and_restart(
    tmp_path: Path, applied_runtime_config
) -> None:
    source = tmp_path / "config.yaml"
    original = default_project_config("applied")
    write_config(source, original)
    applied_runtime_config(source)
    snapshot = configuration.APPLIED_CONFIG_PATH.read_bytes()
    daemon = configuration.SSHD_CONFIG_PATH.read_bytes()
    desired = original.model_copy(deep=True)
    desired.auth.allow_password = False
    desired.sync.interval_seconds = 5
    write_config(source, desired)
    assert configuration.check_desired_provider(source)
    assert configuration.load_runtime_config(source) == original
    applied_runtime_config(source)
    assert configuration.APPLIED_CONFIG_PATH.read_bytes() == snapshot
    assert configuration.SSHD_CONFIG_PATH.read_bytes() == daemon
    if os.name != "nt":
        assert configuration.APPLIED_CONFIG_PATH.stat().st_mode & 0o777 == 0o600
    # Container recreation discards its private layer; only then capture desired settings.
    configuration.APPLIED_CONFIG_PATH.unlink()
    applied_runtime_config(source)
    assert configuration.load_runtime_config(source) == desired
    assert not configuration.check_desired_provider(source)


def test_pending_provider_edit_blocks_manual_refresh(
    tmp_path: Path, applied_runtime_config
) -> None:
    source = tmp_path / "config.yaml"
    config = default_project_config("applied")
    write_config(source, config)
    applied_runtime_config(source)
    config.provider.path = "/etc/sftpwarden/different.yaml"
    write_config(source, config)
    with pytest.raises(RuntimeError, match="requires deploy"):
        configuration.check_desired_provider(source)
    source.write_text("invalid: [", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Desired configuration is invalid"):
        configuration.check_desired_provider(source)
    assert configuration.load_runtime_config(source).provider.path != config.provider.path


def test_invalid_daemon_candidate_keeps_previous_files(
    tmp_path: Path, applied_runtime_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "config.yaml"
    write_config(source, default_project_config("applied"))
    configuration.SSHD_CONFIG_PATH.write_text("previous config", encoding="utf-8")

    def reject(_candidate):
        raise subprocess.CalledProcessError(1, ["sshd", "-t"])

    monkeypatch.setattr(configuration, "validate_sshd_candidate", reject)
    with pytest.raises(subprocess.CalledProcessError):
        applied_runtime_config(source)
    assert not configuration.APPLIED_CONFIG_PATH.exists()
    assert configuration.SSHD_CONFIG_PATH.read_text() == "previous config"
    assert not list(tmp_path.glob(".sftpwarden-sshd-*"))


@pytest.mark.parametrize("snapshot", [None, "{}", "invalid", '{"config":"secret"}'])
def test_missing_or_invalid_applied_config_never_falls_back_to_desired(
    tmp_path: Path, applied_runtime_config, snapshot: str | None
) -> None:
    source = tmp_path / "config.yaml"
    write_config(source, default_project_config("desired"))
    if snapshot is not None:
        configuration.APPLIED_CONFIG_PATH.write_text(snapshot, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Applied runtime configuration") as failure:
        configuration.load_runtime_config(source)
    assert "secret" not in str(failure.value)


def test_applied_config_rejects_another_source_path(tmp_path: Path, applied_runtime_config) -> None:
    source = tmp_path / "config.yaml"
    write_config(source, default_project_config("applied"))
    applied_runtime_config(source)
    with pytest.raises(RuntimeError, match="Applied runtime configuration"):
        configuration.load_runtime_config(tmp_path / "other.yaml")


def test_runtime_snapshot_does_not_expand_dsn_twice(
    tmp_path: Path, applied_runtime_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sftpwarden.config import ProviderType
    from sftpwarden.providers.postgres_provider import PostgreSQLProvider
    from tests.providers.test_sql_providers import (
        FakeConnection,
        FakeCursor,
        install_fake_psycopg,
    )

    source = tmp_path / "config.yaml"
    config = default_project_config(
        "applied", ProviderType.POSTGRESQL, dsn="${SFTPWARDEN_TEST_DSN}"
    )
    intended = "postgresql://test:synthetic-$LITERAL@db/users"
    monkeypatch.setenv("SFTPWARDEN_TEST_DSN", intended)
    monkeypatch.setenv("LITERAL", "must-not-be-expanded-recursively")
    write_config(source, config)
    applied_runtime_config(source)
    applied = configuration.load_runtime_config(source)
    assert applied.provider.dsn == "${SFTPWARDEN_TEST_DSN}"
    connection = FakeConnection(FakeCursor())
    install_fake_psycopg(monkeypatch, connection)
    PostgreSQLProvider(applied.provider).read()
    assert connection.dsn == intended


def test_host_fingerprints_survive_a_stale_single_file_mount(
    tmp_path: Path, applied_runtime_config
) -> None:
    source = tmp_path / "config.yaml"
    desired = default_project_config("applied")
    write_config(source, desired)
    applied_runtime_config(source)
    source.write_text("stale or unavailable mount: [")

    def check():
        provider, complete = runtime_config_fingerprints(desired)
        return configuration.check_desired_provider(
            source, desired_provider_fingerprint=provider, desired_config_fingerprint=complete
        )

    assert not check()
    desired.auth.allow_password = False
    assert check()
    desired.provider.path = "/etc/sftpwarden/provider-data/not-deployed.yaml"
    with pytest.raises(RuntimeError, match="requires deploy"):
        check()


@pytest.mark.parametrize("option", ["desired_provider_fingerprint", "desired_config_fingerprint"])
def test_refresh_requires_a_complete_fingerprint_pair(
    tmp_path: Path, applied_runtime_config, option: str
) -> None:
    source = tmp_path / "config.yaml"
    write_config(source, default_project_config("applied"))
    applied_runtime_config(source)
    with pytest.raises(RuntimeError, match="Both desired"):
        configuration.check_desired_provider(source, **{option: "incomplete"})
