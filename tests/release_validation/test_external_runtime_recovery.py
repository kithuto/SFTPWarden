"""Recover a real external-provider runtime into an independently protected table."""

import os
import shutil

import pytest
import yaml

from sftpwarden.config import load_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.render.compose import compose_model
from sftpwarden.services import backup_runtime
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.users import ProviderUsers, SFTPUser

from .conftest import assert_ok, eventually, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_database_providers import (
    DATABASE_CASES,
    init_when_database_is_ready,
    start_database_container,
)
from .test_fresh_recovery import transfer

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
    pytest.mark.release_databases,
]


@pytest.mark.parametrize("case", DATABASE_CASES, ids=lambda case: case.provider)
def test_external_runtime_capture_fresh_recovery_and_sftp(
    cli,
    cleanup_stack,
    tmp_path,
    unique_name,
    monkeypatch,
    case,
    policy_image,
):
    root = tmp_path / "source"
    connection_root = tmp_path / "new-database"
    recovered = tmp_path / "recovered"
    client = tmp_path / "client"
    client.mkdir(mode=0o700)
    cleanup_stack.add(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    network = "sftpwarden-recovery-" + unique_name
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network]))
    run_external(["docker", "network", "create", network])
    database = "sftpwarden-database-" + unique_name
    replica = case.provider == "mongodb"
    port = start_database_container(
        case,
        name=database,
        host_port=0,
        cleanup_stack=cleanup_stack,
        arguments=("--replSet", "rs0", "--bind_ip_all") if replica else (),
    )
    run_external(["docker", "network", "connect", "--alias", "database", network, database])
    if replica:
        eventually(
            lambda: (
                run_external(
                    [
                        "docker",
                        "exec",
                        database,
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
            ),
            timeout_seconds=90,
            description="external recovery replica startup",
        )
    host_dsn = case.dsn_template.format(port=port) + ("?directConnection=true" if replica else "")
    assert_ok(init_when_database_is_ready(cli, case=case, root=root, dsn=host_dsn))
    runtime_dsn = host_dsn.replace(f"localhost:{port}", f"database:{case.internal_port}")
    runtime_dsn = runtime_dsn.replace(f"127.0.0.1:{port}", f"database:{case.internal_port}")
    monkeypatch.setenv("SFTPWARDEN_HOME", str(cli.home))
    monkeypatch.setenv("RECOVERY_DB_DSN", host_dsn)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backup_runtime, "remote_preparation_image", lambda: policy_image)
    server = PolicyRuntime(client, policy_image, sync=True)
    config = load_config(root / "sftpwarden.yaml")
    config.provider.dsn = "${RECOVERY_DB_DSN}"
    config.docker.image = policy_image
    config.docker.container_name = server.name
    config.auth.allow_password = False
    config.healthcheck.interval_seconds = 1
    write_config(root / "sftpwarden.yaml", config)
    original = provider_from_config(root, config)
    users = ProviderUsers(
        schema_version=config.provider.user_schema,
        users=[
            SFTPUser(username="alice", uid=2301, gid=2301, public_keys=[server.alice_public]),
            SFTPUser(username="control", uid=2302, gid=2302, public_keys=[server.control_public]),
        ],
    )
    original.write(users)
    users = original.read()
    for directory in ("state", "host_keys", "data"):
        (root / directory).mkdir(mode=0o700, exist_ok=True)
    environment = dict(os.environ, RECOVERY_DB_DSN=runtime_dsn)

    def compose(project, selected):
        model = compose_model(selected, project, allow_local_build=False)
        model["name"] = "sftpwarden-external-" + unique_name
        model["networks"] = {"default": {"external": True, "name": network}}
        model["services"]["sftpwarden"]["ports"] = ["127.0.0.1::22"]
        path = project / selected.docker.compose_file
        path.write_text(yaml.safe_dump(model))
        command = ["docker", "compose", "-f", str(path)]
        cleanup_stack.add(lambda: run_external([*command, "down", "--volumes"], env=environment))
        return command

    command = compose(root, config)
    entry = local_context("source", root, config.provider.type)
    save_registry(ContextRegistry(default="source", contexts={"source": entry}))
    run_external(
        [*command, "up", "-d", "--wait", "--wait-timeout", "90"], env=environment, timeout=120
    )
    server.refresh_port()
    public = run_external(
        [
            "docker",
            "exec",
            server.name,
            "cat",
            "/etc/sftpwarden/host_keys/ssh_host_ed25519_key.pub",
        ]
    ).stdout.strip()
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("control").returncode == 0
    payload = client / "payload"
    payload.write_bytes(b"external-provider recovery payload\n")
    batch = client / "transfer.batch"
    batch.write_text(f'put "{payload.as_posix()}" upload/payload\nchmod 600 upload/payload\n')
    transfer(server, batch)
    archive = tmp_path / "runtime.tgz"
    create_backup(context_name="source", output=str(archive), maintenance=True, include_data=True)
    server.refresh_port()
    assert server.connect("control").returncode == 0
    run_external([*command, "down"], env=environment)
    connection_root.mkdir(mode=0o700)
    target = config.model_copy(deep=True)
    if case.provider == "mongodb":
        target.provider.collection = "recovered_users"
    else:
        target.provider.table = "recovered_users"
    write_config(connection_root / "sftpwarden.yaml", target)
    replacement = provider_from_config(connection_root, target)
    replacement.create_table()
    destination = local_context("empty-provider", connection_root, target.provider.type)
    save_registry(
        ContextRegistry(default="empty-provider", contexts={"empty-provider": destination})
    )
    result = restore_backup(
        context_name=None,
        backup_path=str(archive),
        target_root=str(recovered),
        provider_context="empty-provider",
        include_data=True,
    )
    assert result.safety_backup.is_file()
    assert original.read() == users
    assert replacement.read() == users
    restored = load_config(recovered / "sftpwarden.yaml")
    command = compose(recovered, restored)
    run_external(
        [*command, "up", "-d", "--wait", "--wait-timeout", "90"], env=environment, timeout=120
    )
    server.refresh_port()
    # Ephemeral port changes, but the trusted public identity must remain identical.
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("control").returncode == 0
    downloaded = client / "downloaded"
    batch.write_text(f'get upload/payload "{downloaded.as_posix()}"\n')
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    assert (
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "stat",
                "-c",
                "%u:%g:%a",
                "/data/alice/upload/payload",
            ]
        ).stdout.strip()
        == "2301:2301:600"
    )
