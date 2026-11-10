from pathlib import Path

import pytest

from sftpwarden.cli_commands.provider import migrate_provider_schema
from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.providers import provider_from_config
from sftpwarden.services.users import UserService
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils import persistence
from sftpwarden.utils.errors import ProviderError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFMsDFmvcbuEKKQ2nRx6M4jxyJuS9KoOesN7GyrXw+d+"
OTHER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGv5cJFgm0ffn6oHXkIseBv94l3DX7v+2vbF0+qWC+M6"


def schema_project(root, kind):
    config = default_project_config("schema-coordination", kind, user_schema=1)
    path = root / "sftpwarden.yaml"
    write_config(path, config)
    provider = provider_from_config(root, config)
    provider.write(
        ProviderUsers(
            schema_version=1,
            users=[
                SFTPUser(username="alice", public_keys=[KEY]),
                SFTPUser(username="bob", public_keys=[OTHER_KEY]),
            ],
        )
    )
    return path


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_key_lifecycle_migration_updates_config_and_keeps_other_users(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, kind)
    service = UserService(config_path=str(path))
    name = service.list_user_keys("alice")[0].name
    result = service.disable_user_key("alice", name, disabled=True, allow_migration=True)
    current = load_config(path)
    assert result.schema_migrated
    assert current.provider.user_schema == 2
    users = provider_from_config(tmp_path, current).read()
    assert users.users[0].keys[0].disabled
    assert users.users[1].keys[0].public_key == OTHER_KEY
    assert service.list_users() == users


def test_interrupted_key_migration_resumes_without_losing_named_key_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    service = UserService(config_path=str(path))
    name = service.list_user_keys("alice")[0].name
    replace = persistence.os.replace
    interrupted = False

    def interrupt_config_publication(source, destination):
        nonlocal interrupted
        if Path(destination) == path and not interrupted:
            interrupted = True
            raise OSError("interrupted config publication")
        return replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", interrupt_config_publication)
    with pytest.raises(OSError, match="interrupted config publication"):
        service.disable_user_key("alice", name, disabled=True, allow_migration=True)
    assert load_config(path).provider.user_schema == 1
    result = migrate_provider_schema(
        to_schema=2,
        context=None,
        config=str(path),
        backup=True,
        yes=True,
        dry_run=False,
    )
    assert result["changed"]
    current = load_config(path)
    assert current.provider.user_schema == 2
    users = provider_from_config(tmp_path, current).read()
    assert users.users[0].keys[0].disabled
    assert users.users[1].keys[0].public_key == OTHER_KEY


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_pending_migration_blocks_edits_and_conflicting_recovery(tmp_path, monkeypatch, kind):
    from sftpwarden.services.schema_transition import transition_path

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, kind)
    service = UserService(config_path=str(path))
    key_name = service.list_user_keys("alice")[0].name
    replace = persistence.os.replace

    def interrupt_config(source, destination):
        if Path(destination) == path:
            raise OSError("interrupted config publication")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", interrupt_config)
        with pytest.raises(OSError, match="interrupted config publication"):
            service.disable_user_key("alice", key_name, disabled=True, allow_migration=True)
    journal = transition_path(service.entry)
    before = journal.read_bytes()
    with pytest.raises(ProviderError, match="incomplete"):
        service.remove_user("bob")
    target_config = load_config(path)
    target_config.provider.user_schema = 2
    target_provider = provider_from_config(tmp_path, target_config)
    changed = target_provider.read()
    changed.users[1].comment = "external edit after interruption"
    target_provider.write(changed)
    with pytest.raises(ProviderError, match="changed during an incomplete"):
        migrate_provider_schema(
            to_schema=2,
            context=None,
            config=str(path),
            backup=True,
            yes=True,
            dry_run=False,
        )
    assert target_provider.read() == changed
    assert load_config(path).provider.user_schema == 1
    assert journal.read_bytes() == before


def test_migration_dry_run_does_not_write_data_config_or_recovery(tmp_path, monkeypatch):
    from sftpwarden.services.schema_transition import transition_path

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    before = path.read_bytes()
    service = UserService(config_path=str(path))
    users = service.list_users()
    result = service.disable_user_key(
        "alice",
        service.list_user_keys("alice")[0].name,
        disabled=True,
        allow_migration=True,
        dry_run=True,
    )
    assert result.schema_migrated and result.dry_run
    assert path.read_bytes() == before
    assert service.list_users() == users
    assert not transition_path(service.entry).exists()


def test_legacy_sql_projection_does_not_hide_completed_v2_migration(tmp_path, monkeypatch):
    from sftpwarden.providers.schema import require_configured_schema

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    legacy_provider = provider_from_config(tmp_path, load_config(path))
    service = UserService(config_path=str(path))
    service.disable_user_key(
        "alice",
        service.list_user_keys("alice")[0].name,
        disabled=True,
        allow_migration=True,
    )
    with pytest.raises(ProviderError, match="differs"):
        require_configured_schema(legacy_provider)


@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_import_promotion_preserves_disabled_policy_and_requires_deploy(
    tmp_path,
    monkeypatch,
    mode,
):
    from sftpwarden.services import provider_transfer
    from sftpwarden.users.schemas import migrate_provider_users

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    service = UserService(config_path=str(path))
    source = migrate_provider_users(service.list_users(), to_version=2)
    source.users[0].keys[0].disabled = True
    calls = []
    monkeypatch.setattr(provider_transfer, "refresh_context", lambda entry: calls.append(entry))
    result = provider_transfer.write_provider_users(
        entry=service.entry,
        config=service.config,
        provider=service.provider,
        source_users=source,
        mode=mode,
        dry_run=False,
        no_refresh=False,
    )
    assert result.deploy_required
    assert result.refresh_output is None and not calls
    current = load_config(path)
    assert current.provider.user_schema == 2
    assert provider_from_config(tmp_path, current).read() == source


def test_cli_migration_reports_deploy_without_refresh(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sftpwarden.cli import app
    from sftpwarden.cli_commands import users as commands

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.YAML)
    service = UserService(config_path=str(path))
    name = service.list_user_keys("alice")[0].name
    calls = []
    monkeypatch.setattr(
        commands,
        "print_refresh_after_user_change",
        lambda entry: calls.append(entry),
    )
    result = CliRunner().invoke(
        app,
        ["user", "key", "disable", "alice", name, "--config", str(path), "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "sftpwarden deploy" in result.output
    assert not calls


def test_legacy_import_does_not_downgrade_v2_destination(tmp_path, monkeypatch):
    from sftpwarden.services.provider_transfer import write_provider_users

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    service = UserService(config_path=str(path))
    legacy = service.list_users()
    service.disable_user_key(
        "alice",
        service.list_user_keys("alice")[0].name,
        disabled=True,
        allow_migration=True,
    )
    write_provider_users(
        entry=service.entry,
        config=service.config,
        provider=service.provider,
        source_users=legacy,
        mode="replace",
        dry_run=False,
        no_refresh=True,
    )
    current = service.provider.read()
    assert current.schema_version == 2
    assert current.users[0].keys[0].public_key == KEY
    assert load_config(path).provider.user_schema == 2


def test_import_migration_accepts_sql_row_order_without_losing_metadata(tmp_path, monkeypatch):
    from sftpwarden.services.provider_transfer import write_provider_users
    from sftpwarden.users import SFTPUserKey

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    service = UserService(config_path=str(path))
    source = ProviderUsers(
        schema_version=2,
        users=[
            SFTPUser(
                username="zed",
                keys=[
                    SFTPUserKey(
                        name="z", public_key=KEY, disabled=True, metadata={"owner": "test"}
                    ),
                    SFTPUserKey(name="a", public_key=OTHER_KEY),
                ],
            ),
            SFTPUser(username="alice", keys=[], disabled=True),
        ],
    )
    result = write_provider_users(
        entry=service.entry,
        config=service.config,
        provider=service.provider,
        source_users=source,
        mode="replace",
        dry_run=False,
        no_refresh=True,
    )
    assert result.deploy_required
    current = load_config(path)
    stored = provider_from_config(tmp_path, current).read()
    assert [user.username for user in stored.users] == ["alice", "zed"]
    assert stored.users[1].keys[1].disabled
    assert stored.users[1].keys[1].metadata == {"owner": "test"}


def test_prepared_migration_retries_before_any_provider_write(tmp_path, monkeypatch):
    from sftpwarden.services.schema_transition import transition_path

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    path = schema_project(tmp_path, ProviderType.SQLITE)
    service = UserService(config_path=str(path))
    original = service.list_users()
    name = service.list_user_keys("alice")[0].name

    def fail_before_write(*args):
        raise KeyboardInterrupt("interrupted before provider write")

    with monkeypatch.context() as patch:
        patch.setattr(service.provider, "replace_if_unchanged", fail_before_write)
        with pytest.raises(KeyboardInterrupt, match="interrupted before"):
            service.disable_user_key("alice", name, disabled=True, allow_migration=True)
    assert service.provider.read() == original
    assert transition_path(service.entry).exists()
    migrate_provider_schema(
        to_schema=2,
        context=None,
        config=str(path),
        backup=True,
        yes=True,
        dry_run=False,
    )
    assert not transition_path(service.entry).exists()
    assert UserService(config_path=str(path)).list_user_keys("alice")[0].disabled


def test_rejected_mongo_migration_does_not_block_individual_edits(
    tmp_path,
    monkeypatch,
    install_fake_pymongo,
):
    from sftpwarden.services.schema_transition import transition_path

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    install_fake_pymongo(replica_set=False)
    config = default_project_config(
        "mongo",
        ProviderType.MONGODB,
        dsn="mongodb://localhost/test",
        user_schema=1,
    )
    path = tmp_path / "sftpwarden.yaml"
    write_config(path, config)
    service = UserService(config_path=str(path))
    service.provider.create_table()
    service.add_user(username="alice", public_keys=[KEY])
    name = service.list_user_keys("alice")[0].name
    with pytest.raises(ProviderError, match="requires a replica set"):
        service.disable_user_key("alice", name, disabled=True, allow_migration=True)
    assert not transition_path(service.entry).exists()
    service.set_user_disabled("alice", disabled=True)
    assert service.show_user("alice").disabled
