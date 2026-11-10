"""Real read-only SQL discovery and recovery after a committed v2 migration."""

from contextlib import closing
from pathlib import Path

import pytest

from sftpwarden.cli_commands.provider import migrate_provider_schema
from sftpwarden.config import load_config
from sftpwarden.providers import load_users, provider_from_config
from sftpwarden.services.users import UserService
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils import persistence
from sftpwarden.utils.errors import ProviderError

from .conftest import PUBLIC_KEY_1, PUBLIC_KEY_2, assert_ok, run_external
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


@pytest.mark.parametrize("case", DATABASE_CASES[:3], ids=lambda case: case.provider)
def test_read_only_schema_discovery_and_interrupted_migration_recovery(
    cli,
    cleanup_stack,
    tmp_path,
    unique_name,
    case,
    monkeypatch,
):
    name = f"sftpwarden-schema-{unique_name}"
    port = start_database_container(case, name=name, host_port=0, cleanup_stack=cleanup_stack)
    root = tmp_path / "project"
    dsn = case.dsn_template.format(port=port)
    assert_ok(init_when_database_is_ready(cli, case=case, root=root, dsn=dsn, user_schema=1))
    path = root / "sftpwarden.yaml"
    config = load_config(path)
    provider = provider_from_config(root, config)
    initial = ProviderUsers(
        schema_version=1,
        users=[
            SFTPUser(username="alice", public_keys=[PUBLIC_KEY_1]),
            SFTPUser(username="bob", public_keys=[PUBLIC_KEY_2]),
        ],
    )
    provider.write(initial)
    if case.provider == "postgresql":
        import psycopg

        with closing(psycopg.connect(dsn)) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE ROLE schema_reader LOGIN PASSWORD 'ephemeral-reader'")
            cursor.execute("GRANT CONNECT ON DATABASE sftpwarden TO schema_reader")
            cursor.execute("GRANT USAGE ON SCHEMA public TO schema_reader")
            cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO schema_reader")
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO schema_reader"
            )
            connection.commit()
    else:
        client = "mariadb" if case.provider == "mariadb" else "mysql"
        run_external(
            [
                "docker",
                "exec",
                "--env",
                "MYSQL_PWD=sftpwarden-root",
                name,
                client,
                "--user=root",
                "--execute",
                "CREATE USER 'schema_reader'@'%' IDENTIFIED BY 'ephemeral-reader'; "
                "GRANT SELECT ON sftpwarden.* TO 'schema_reader'@'%';",
            ]
        )
    reader_dsn = dsn.replace("sftpwarden:sftpwarden@", "schema_reader:ephemeral-reader@")

    def read_only(version):
        return load_users(
            config.provider.type,
            config.provider.path,
            dsn=reader_dsn,
            user_schema=version,
            require_schema_match=True,
        )

    assert read_only(1) == initial
    reader_config = config.model_copy(
        update={
            "provider": config.provider.model_copy(update={"dsn": reader_dsn}),
        }
    )
    with pytest.raises(ProviderError):
        provider_from_config(root, reader_config).write(initial)
    assert provider.read() == initial

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
    assert load_config(path).provider.user_schema == 1
    with pytest.raises(ProviderError, match="differs"):
        read_only(1)
    migrate_provider_schema(
        to_schema=2,
        context=None,
        config=str(path),
        backup=True,
        yes=True,
        dry_run=False,
    )
    assert load_config(path).provider.user_schema == 2
    observed = read_only(2)
    assert observed.users[0].keys[0].disabled
    assert observed.users[1].keys[0].public_key == PUBLIC_KEY_2
