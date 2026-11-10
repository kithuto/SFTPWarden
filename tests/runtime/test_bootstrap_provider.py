"""Reject invalid bootstrap input before attempting any privileged publication."""

import sys
from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config
from sftpwarden.providers import provider_for_config
from sftpwarden.runtime import bootstrap_provider as bootstrap
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.errors import ProviderError, RuntimeError


@pytest.mark.parametrize("content", ["users:", "users: [", "schema_version: 1\nusers: []\n"])
def test_invalid_bootstrap_does_not_touch_the_provider_volume(tmp_path, monkeypatch, content):
    config = default_project_config("bootstrap")
    config.provider.path = "/etc/sftpwarden/provider-data/users.yaml"
    (tmp_path / "users.yaml").write_text(content)
    monkeypatch.setattr(
        bootstrap,
        "open_private_directory",
        lambda *_args: pytest.fail("privileged publication started before validation"),
    )
    with pytest.raises((ProviderError, RuntimeError)):
        bootstrap.bootstrap_provider(config, tmp_path)


def test_missing_bootstrap_does_not_become_empty_policy(tmp_path, monkeypatch):
    config = default_project_config("bootstrap")
    config.provider.path = "/etc/sftpwarden/provider-data/users.yaml"
    monkeypatch.setattr(
        bootstrap, "open_private_directory", lambda *_args: pytest.fail("provider volume touched")
    )
    with pytest.raises(FileNotFoundError):
        bootstrap.bootstrap_provider(config, tmp_path)


def test_bootstrap_refuses_paths_outside_dedicated_volume(tmp_path):
    config = default_project_config("bootstrap")
    config.provider.path = "/etc/sftpwarden/users.yaml"
    with pytest.raises(RuntimeError, match="dedicated"):
        bootstrap.bootstrap_provider(config, tmp_path)


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_valid_bootstrap_publishes_documents_and_preserves_sqlite(
    linux_publication, tmp_path, kind
):
    case = linux_publication
    case.attach(bootstrap)
    config = default_project_config("bootstrap", kind)
    filename = {
        ProviderType.YAML: "users.yaml",
        ProviderType.CSV: "users.csv",
        ProviderType.SQLITE: "users.sqlite",
    }[kind]
    source = tmp_path / "source"
    source.mkdir()
    path = (case.directory if kind == ProviderType.SQLITE else source) / filename
    provider_for_config(config.provider, path).write(ProviderUsers(schema_version=2, users=[]))
    before = path.read_bytes()
    config.provider.path = "/etc/sftpwarden/provider-data/" + filename
    bootstrap.bootstrap_provider(config, source)
    assert (case.directory / filename).read_bytes() == before
    assert case.closed == [case.descriptor]
    assert len(case.changes) == 2


def test_bootstrap_database_is_external_and_never_touches_files(tmp_path, monkeypatch):
    config = default_project_config(
        "bootstrap", ProviderType.POSTGRESQL, dsn="postgresql://db/test"
    )
    monkeypatch.setattr(
        bootstrap, "open_private_directory", lambda *a: pytest.fail("external provider modified")
    )
    bootstrap.bootstrap_provider(config, tmp_path)


def test_bootstrap_rejects_oversized_document_before_publication(tmp_path, monkeypatch):
    config = default_project_config("bootstrap")
    config.provider.path = "/etc/sftpwarden/provider-data/users.yaml"
    (tmp_path / "users.yaml").write_text("schema_version: 2\nusers: []\n")
    monkeypatch.setattr(bootstrap, "MAX_METADATA_BYTES", 2)
    with pytest.raises(RuntimeError, match="size"):
        bootstrap.bootstrap_provider(config, tmp_path)


@pytest.mark.parametrize("failure", [False, True])
def test_bootstrap_entrypoint_redacts_source_errors(monkeypatch, failure):
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--config", "selected.yaml"])
    config = default_project_config("bootstrap")
    monkeypatch.setattr(bootstrap, "load_config", lambda path: config)
    calls = []

    def run(selected, directory):
        calls.append((selected, directory))
        if failure:
            raise OSError("private-sentinel")

    monkeypatch.setattr(bootstrap, "bootstrap_provider", run)
    if failure:
        with pytest.raises(SystemExit, match="bootstrap failed") as caught:
            bootstrap.main()
        assert "private-sentinel" not in str(caught.value)
    else:
        bootstrap.main()
    assert calls == [(config, Path("."))]
