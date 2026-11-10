"""T11: real SFTP with database credentials that cannot mutate either schema."""

from contextlib import closing
from dataclasses import replace

import pytest
import yaml

from sftpwarden.config import ProviderConfig
from sftpwarden.providers import provider_for_config
from sftpwarden.users import SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError

from .conftest import assert_ok, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_database_providers import (
    DATABASE_CASES,
    init_when_database_is_ready,
    start_database_container,
)

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
    pytest.mark.release_databases,
]


@pytest.mark.parametrize("case", DATABASE_CASES, ids=lambda case: case.provider)
def test_v1_v2_runtime_reads_with_database_write_permissions_denied(
    cli, tmp_path, unique_name, cleanup_stack, policy_image, case
):
    if case.provider == "mongodb":
        case = replace(
            case,
            environment={
                "MONGO_INITDB_ROOT_USERNAME": "sftpwarden",
                "MONGO_INITDB_ROOT_PASSWORD": "sftpwarden",
            },
            dsn_template="mongodb://sftpwarden:sftpwarden@127.0.0.1:{port}/sftpwarden?authSource=admin",
        )
    network = "sftpwarden-readonly-" + unique_name
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network]))
    run_external(["docker", "network", "create", network])
    name = "sftpwarden-readonly-db-" + unique_name
    port = start_database_container(case, name=name, host_port=0, cleanup_stack=cleanup_stack)
    run_external(["docker", "network", "connect", "--alias", "database", network, name])
    dsn = case.dsn_template.format(port=port)
    assert_ok(init_when_database_is_ready(cli, case=case, root=tmp_path / "project", dsn=dsn))
    if case.provider == "postgresql":
        import psycopg

        with closing(psycopg.connect(dsn)) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE ROLE schema_reader LOGIN PASSWORD 'ephemeral-reader'")
            cursor.execute("GRANT CONNECT ON DATABASE sftpwarden TO schema_reader")
            cursor.execute("GRANT USAGE ON SCHEMA public TO schema_reader")
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO schema_reader"
            )
            connection.commit()
    elif case.provider == "mongodb":
        provider = provider_for_config(ProviderConfig(type="mongodb", dsn=dsn))
        database = provider._database()
        try:
            database.command(
                "createUser",
                "schema_reader",
                pwd="ephemeral-reader",  # noqa: S106 - disposable database credential
                roles=[{"role": "read", "db": "sftpwarden"}],
            )
        finally:
            database.client.close()
    else:
        run_external(
            [
                "docker",
                "exec",
                "--env",
                "MYSQL_PWD=sftpwarden-root",
                name,
                "mariadb" if case.provider == "mariadb" else "mysql",
                "--user=root",
                "--execute",
                "CREATE USER 'schema_reader'@'%' IDENTIFIED BY 'ephemeral-reader'; "
                "GRANT SELECT ON sftpwarden.* TO 'schema_reader'@'%';",
            ]
        )
    reader_dsn = dsn.replace("sftpwarden:sftpwarden@", "schema_reader:ephemeral-reader@").replace(
        "authSource=admin", "authSource=sftpwarden"
    )
    for schema in (1, 2):
        root = tmp_path / f"runtime-{schema}"
        root.mkdir(mode=0o700)
        server = PolicyRuntime(root, policy_image, sync=True)
        cleanup_stack.add(server.close)
        namespace = {"collection" if case.provider == "mongodb" else "table": f"readonly_v{schema}"}
        config = ProviderConfig(type=case.provider, dsn=dsn, user_schema=schema, **namespace)
        writer = provider_for_config(config)
        writer.create_table()
        for username, key in (("alice", server.alice_public), ("control", server.control_public)):
            user = (
                SFTPUser(username=username, public_keys=[key])
                if schema == 1
                else SFTPUser(username=username, keys=[SFTPUserKey(name="access", public_key=key)])
            )
            writer.upsert_user(user)
        expected = writer.read()
        reader = provider_for_config(config.model_copy(update={"dsn": reader_dsn}))
        assert reader.read() == expected
        with pytest.raises(ProviderError):
            reader.upsert_user(expected.users[0].model_copy(update={"comment": "not allowed"}))
        assert writer.read() == expected
        runtime_dsn = reader_dsn.replace(f"127.0.0.1:{port}", f"database:{case.internal_port}")
        path = server.inputs / "config.yaml"
        settings = yaml.safe_load(path.read_text())
        settings["provider"] = dict(
            type=case.provider, dsn=runtime_dsn, user_schema=schema, **namespace
        )
        path.write_text(yaml.safe_dump(settings))
        server.start(docker_args=("--network", network))
        assert server.connect("alice").returncode == 0
        assert server.connect("control").returncode == 0
        assert writer.read() == expected
        server.close()
