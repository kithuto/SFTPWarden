"""Existing database restore uses checked provider transactions and durable recovery points."""

import json
import shutil

import pytest

from sftpwarden.config import load_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.services import restore_existing
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.users import SFTPUser
from sftpwarden.utils.errors import RuntimeError

from .conftest import assert_ok, eventually, run_external
from .test_database_providers import (
    DATABASE_CASES,
    init_when_database_is_ready,
    start_database_container,
)

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_databases,
]
CASES = [(case, False) for case in DATABASE_CASES] + [(DATABASE_CASES[-1], True)]


@pytest.mark.parametrize(
    "case,replica",
    CASES,
    ids=[case.provider + ("-replica" if replica else "") for case, replica in CASES],
)
def test_existing_database_restore_and_stale_snapshot_protection(
    cli,
    cleanup_stack,
    tmp_path,
    unique_name,
    monkeypatch,
    case,
    replica,
):
    root = tmp_path / "project"
    for path in (root, cli.home, cli.cwd):
        cleanup_stack.add(lambda path=path: shutil.rmtree(path, ignore_errors=True))
    name = f"sftpwarden-restore-{unique_name}"
    port = start_database_container(
        case,
        name=name,
        host_port=0,
        cleanup_stack=cleanup_stack,
        arguments=("--replSet", "rs0", "--bind_ip_all") if replica else (),
    )
    if replica:

        def initialize_replica():
            return (
                run_external(
                    [
                        "docker",
                        "exec",
                        name,
                        "mongosh",
                        "--quiet",
                        "--eval",
                        "try { rs.status(); } catch (e) { "
                        "rs.initiate({_id:'rs0',members:[{_id:0,host:'localhost:27017'}]}); }",
                    ],
                    check=False,
                    timeout=15,
                ).returncode
                == 0
            )

        eventually(initialize_replica, timeout_seconds=90, description="restore replica startup")
    dsn = case.dsn_template.format(port=port) + ("?directConnection=true" if replica else "")
    assert_ok(init_when_database_is_ready(cli, case=case, root=root, dsn=dsn))
    monkeypatch.setenv("SFTPWARDEN_HOME", str(cli.home))
    monkeypatch.chdir(cli.cwd)
    config = load_config(root / "sftpwarden.yaml")
    provider = provider_from_config(root, config)
    provider.mutate_user(provider.read(), "alice", SFTPUser(username="alice", disabled=True))
    snapshot = provider.read()
    archive = cli.cwd / "source.tar.gz"
    create_backup(context_name=case.provider, output=str(archive))
    provider.mutate_user(provider.read(), "bob", SFTPUser(username="bob", disabled=True))
    if case.provider == "mongodb" and not replica:
        before = provider.read()
        with pytest.raises(RuntimeError, match="interrupted"):
            restore_backup(context_name=case.provider, backup_path=str(archive))
        assert provider.read() == before
        return
    result = restore_backup(context_name=case.provider, backup_path=str(archive))
    assert provider.read() == snapshot
    assert result.safety_backup.is_file()
    journal = json.loads((root / restore_existing.JOURNAL).read_text())
    assert journal["phase"] == "files_restored"
    assert restore_existing.PROVIDER_STEP in journal["completed"]
    replace = provider.replace_if_unchanged

    def concurrent_replace(expected, replacement):
        provider.mutate_user(
            provider.read(),
            "concurrent",
            SFTPUser(
                username="concurrent",
                disabled=True,
            ),
        )
        replace(expected, replacement)

    monkeypatch.setattr(provider, "replace_if_unchanged", concurrent_replace)
    monkeypatch.setattr(restore_existing, "provider_from_config", lambda *_args: provider)
    with pytest.raises(RuntimeError, match="interrupted"):
        restore_backup(context_name=case.provider, backup_path=str(archive))
    assert {user.username for user in provider.read().users} == {"alice", "concurrent"}


@pytest.mark.parametrize(
    "case,replica",
    CASES,
    ids=[case.provider + ("-replica" if replica else "") for case, replica in CASES],
)
def test_fresh_database_recovery_requires_explicit_empty_protected_namespace(
    cli,
    cleanup_stack,
    tmp_path,
    unique_name,
    monkeypatch,
    case,
    replica,
):
    root = tmp_path / "source"
    target = tmp_path / "connection"
    recovered = tmp_path / "recovered"
    cleanup_stack.add(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    name = "sftpwarden-fresh-db-" + unique_name
    port = start_database_container(
        case,
        name=name,
        host_port=0,
        cleanup_stack=cleanup_stack,
        arguments=("--replSet", "rs0", "--bind_ip_all") if replica else (),
    )
    if replica:
        eventually(
            lambda: (
                run_external(
                    [
                        "docker",
                        "exec",
                        name,
                        "mongosh",
                        "--quiet",
                        "--eval",
                        (
                            "try { rs.status(); } catch (e) { "
                            "rs.initiate({_id:'rs0',members:[{_id:0,host:'localhost:27017'}]}); }"
                        ),
                    ],
                    check=False,
                    timeout=15,
                ).returncode
                == 0
            ),
            timeout_seconds=90,
            description="fresh recovery replica startup",
        )
    dsn = case.dsn_template.format(port=port) + ("?directConnection=true" if replica else "")
    assert_ok(init_when_database_is_ready(cli, case=case, root=root, dsn=dsn))
    monkeypatch.setenv("SFTPWARDEN_HOME", str(cli.home))
    monkeypatch.chdir(cli.cwd)
    config = load_config(root / "sftpwarden.yaml")
    original = provider_from_config(root, config)
    original.mutate_user(original.read(), "alice", SFTPUser(username="alice", disabled=True))
    source_users = original.read()
    archive = cli.cwd / "source.tgz"
    create_backup(context_name=case.provider, output=str(archive))
    target.mkdir(mode=0o700)
    isolated = config.model_copy(deep=True)
    if case.provider == "mongodb":
        isolated.provider.collection = "recovery_users"
    else:
        isolated.provider.table = "recovery_users"
    write_config(target / "sftpwarden.yaml", isolated)
    replacement = provider_from_config(target, isolated)
    replacement.create_table()
    context = local_context("new-provider", target, isolated.provider.type)
    save_registry(ContextRegistry(default="new-provider", contexts={"new-provider": context}))
    assert replacement.read().users == []
    if case.provider == "mongodb" and not replica:
        with pytest.raises(RuntimeError, match="incomplete"):
            restore_backup(
                context_name=None,
                backup_path=str(archive),
                target_root=str(recovered),
                provider_context="new-provider",
            )
        assert replacement.read().users == []
        assert original.read() == source_users
        assert not recovered.exists()
        return
    result = restore_backup(
        context_name=None,
        backup_path=str(archive),
        target_root=str(recovered),
        provider_context="new-provider",
    )
    assert original.read() == source_users
    assert replacement.read() == source_users
    assert result.safety_backup.is_file()
    assert load_config(recovered / "sftpwarden.yaml").provider == isolated.provider
    receipt = json.loads(recovered.with_name(".recovered.provider-recovery.json").read_text())
    assert receipt["phase"] == "files_restored_activation_required"
