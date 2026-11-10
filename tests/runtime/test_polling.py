"""Metadata wakes preserve periodic expiry and never watch deployment config."""

from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config
from sftpwarden.runtime import polling


@pytest.fixture
def clock(monkeypatch):
    instant = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        instant[0] += seconds

    monkeypatch.setattr(polling.time, "monotonic", lambda: instant[0])
    monkeypatch.setattr(polling.time, "sleep", sleep)
    return instant, sleeps


@pytest.fixture
def config(tmp_path):
    config = default_project_config("polling")
    config.sync.enabled = True
    config.provider.path = str(tmp_path / "users.yaml")
    Path(config.provider.path).write_text("users: []\n")
    return config


def test_unchanged_file_and_config_edit_keep_periodic_deadline(config, tmp_path, clock):
    snapshot = polling.provider_stamp(config)
    (tmp_path / "sftpwarden.yaml").write_text("unapplied config change")
    polling.wait_for_provider_change(config, snapshot, 2.5)
    assert clock[1] == [1, 1, 0.5]


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_atomic_replacement_wakes_even_with_same_size_and_timestamp(config, clock, kind):
    import os

    config.provider.type = kind
    path = Path(config.provider.path)
    before = polling.provider_stamp(config)
    replacement = path.with_suffix(".next")
    replacement.write_bytes(path.read_bytes())
    info = path.stat()
    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
    replacement.replace(path)
    polling.wait_for_provider_change(config, before, 60)
    assert not clock[1]


def test_edit_during_wait_wakes_without_waiting_a_full_interval(config, clock, monkeypatch):
    stamp = polling.provider_stamp(config)
    original = polling.time.sleep

    def edit(seconds):
        original(seconds)
        Path(config.provider.path).write_text("users: [changed]\n")

    monkeypatch.setattr(polling.time, "sleep", edit)
    polling.wait_for_provider_change(config, stamp, 60)
    assert clock[1] == [1]


def test_missing_and_recreated_file_wake_without_treating_failure_as_empty(config, clock):
    stamp = polling.provider_stamp(config)
    path = Path(config.provider.path)
    path.unlink()
    assert polling.provider_stamp(config) is None
    polling.wait_for_provider_change(config, stamp, 60)
    path.write_text("users: []\n")
    polling.wait_for_provider_change(config, None, 60)
    assert not clock[1]


@pytest.mark.parametrize("disabled", [False, True])
def test_external_provider_or_disabled_sync_sleeps_without_file_observation(
    config, clock, monkeypatch, disabled
):
    config.sync.enabled = not disabled
    if not disabled:
        config.provider.type = ProviderType.POSTGRESQL
    monkeypatch.setattr(Path, "stat", lambda _: pytest.fail("observed unused provider file"))
    assert polling.provider_stamp(config) is None
    polling.wait_for_provider_change(config, None, 5)
    assert clock[1] == [5]


def test_unreadable_file_does_not_terminate_the_polling_worker(config, monkeypatch):
    def denied(_):
        raise PermissionError("private file")

    monkeypatch.setattr(Path, "stat", denied)
    assert polling.provider_stamp(config) is None


def test_overrun_has_no_extra_wait(config, clock):
    polling.wait_for_provider_change(config, polling.provider_stamp(config), 0)
    assert not clock[1]
