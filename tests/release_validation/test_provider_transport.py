"""Real TLS, environment delivery, authentication and stalled-driver checks."""

from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
import yaml

from sftpwarden.config import ProviderConfig, default_project_config
from sftpwarden.providers import (
    MongoDBProvider,
    MySQLProvider,
    PostgreSQLProvider,
    provider_for_config,
)
from sftpwarden.render.compose import compose_model
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError

from .conftest import (
    CleanupStack,
    eventually,
    remove_docker_container,
    require_executable,
    run_external,
)
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_database_providers import DATABASE_CASES, DatabaseCase

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_databases,
]


def create_tls_material(root: Path) -> None:
    """Create disposable CA/server keys, never copied into an image layer."""
    require_executable("openssl")
    root.mkdir(mode=0o700)
    for ca in ("ca", "untrusted"):
        run_external(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                f"/CN=Disposable {ca}",
                "-keyout",
                str(root / f"{ca}.key"),
                "-out",
                str(root / f"{ca}.pem"),
            ]
        )
        (root / f"{ca}.key").chmod(0o600)
    run_external(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=database",
            "-keyout",
            str(root / "server.key"),
            "-out",
            str(root / "server.csr"),
        ]
    )
    (root / "server.key").chmod(0o600)
    (root / "extensions.cnf").write_text("subjectAltName=DNS:localhost,DNS:database\n")
    run_external(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(root / "server.csr"),
            "-CA",
            str(root / "ca.pem"),
            "-CAkey",
            str(root / "ca.key"),
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            str(root / "extensions.cnf"),
            "-out",
            str(root / "server.pem"),
        ]
    )


@pytest.mark.release_docker
@pytest.mark.parametrize("case", DATABASE_CASES[1:3], ids=["mysql", "mariadb"])
def test_t20_mysql_tls_and_compose_environment_reach_real_sftp(
    case: DatabaseCase,
    policy_image: str,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
) -> None:
    root = tmp_path / "transport project"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    tls = root / "private tls"
    create_tls_material(tls)
    name = f"sftpwarden-transport-{uuid4().hex[:12]}"
    network = f"{name}-network"
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network], timeout=60))
    run_external(["docker", "network", "create", network])
    existed = run_external(["docker", "image", "inspect", case.image], check=False).returncode == 0
    if not existed:
        cleanup_stack.add(lambda: run_external(["docker", "image", "rm", case.image], timeout=120))
        run_external(["docker", "pull", case.image], timeout=300)
    metadata = json.loads(run_external(["docker", "image", "inspect", case.image]).stdout)[0]
    entrypoint = metadata["Config"]["Entrypoint"]
    assert len(entrypoint) == 1
    database_command = metadata["Config"]["Cmd"]
    assert database_command in (["mysqld"], ["mariadbd"])
    cleanup_stack.add(lambda: remove_docker_container(name))
    # The official entrypoint starts only after private material is copied and owned by mysql.
    script = 'while [ ! -f /tmp/tls-ready ]; do sleep 0.1; done; exec "$@"'
    environment = dict(os.environ, **case.environment)
    password_name = "MYSQL_PASSWORD" if case.provider == "mysql" else "MARIADB_PASSWORD"
    environment[password_name] = "transport-sentinel$literal;&%"
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        "database",
        "-p",
        "127.0.0.1::3306",
        "--entrypoint",
        "/bin/sh",
    ]
    for variable in case.environment:
        command.extend(["--env", variable])
    command.extend(
        [
            case.image,
            "-ec",
            script,
            "sh",
            entrypoint[0],
            *database_command,
            "--ssl-ca=/tmp/tls/ca.pem",
            "--ssl-cert=/tmp/tls/server.pem",
            "--ssl-key=/tmp/tls/server.key",
            "--require-secure-transport=ON",
        ]
    )
    run_external(command, env=environment)
    # These fixed paths exist only inside this test's uniquely owned container.
    run_external(["docker", "exec", name, "mkdir", "-m", "700", "/tmp/tls"])  # noqa: S108
    for filename in ("ca.pem", "server.pem", "server.key"):
        run_external(["docker", "cp", str(tls / filename), f"{name}:/tmp/tls/{filename}"])
    run_external(["docker", "exec", name, "chown", "-R", "mysql:mysql", "/tmp/tls"])  # noqa: S108
    run_external(["docker", "exec", name, "touch", "/tmp/tls-ready"])  # noqa: S108
    port = int(run_external(["docker", "port", name, "3306/tcp"]).stdout.strip().rsplit(":", 1)[1])
    password = quote(environment[password_name], safe="")
    base = f"{case.provider}://sftpwarden:{password}@localhost:{port}/sftpwarden"
    query = "?ssl_ca=" + quote(str(tls / "ca.pem"), safe="") + "&read_timeout=3"
    provider = provider_for_config(
        ProviderConfig(type=case.provider, dsn=base + query, user_schema=2)
    )

    def ready() -> bool:
        state = json.loads(
            run_external(["docker", "inspect", "--format", "{{json .State}}", name]).stdout
        )
        assert state["Running"], f"Database exited before TLS readiness: {state['ExitCode']}"
        try:
            provider.create_table()
            return True
        except ProviderError:
            return False

    eventually(ready, timeout_seconds=180, description="verified TLS database connection")
    assert provider.read().users == []
    for bad_dsn in (
        base + "?ssl_ca=" + quote(str(tls / "untrusted.pem"), safe=""),
        base.replace("@localhost:", "@127.0.0.1:") + query,
    ):
        invalid = provider_for_config(
            ProviderConfig(type=case.provider, dsn=bad_dsn, user_schema=2)
        )
        with pytest.raises(ProviderError, match="TLS certificate verification failed"):
            invalid.read()
        # Authentication is a live positive control after each TLS failure.
        assert provider.read().users == []

    server = PolicyRuntime(root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    users = ProviderUsers(
        schema_version=2,
        users=[
            SFTPUser(
                username="alice", keys=[SFTPUserKey(name="alice", public_key=server.alice_public)]
            ),
            SFTPUser(
                username="control",
                keys=[SFTPUserKey(name="control", public_key=server.control_public)],
            ),
        ],
    )
    provider.write(users)
    config = default_project_config("transport")
    config.provider = ProviderConfig(type=case.provider, dsn="${DB_URL}", user_schema=2)
    config.docker.image = policy_image
    config.docker.container_name = server.name
    config.auth.allow_password = False
    config.sync.enabled = True
    config.sync.interval_seconds = 5
    (root / "sftpwarden.yaml").write_text(yaml.safe_dump(config.model_dump(mode="json")))
    for directory in ("data", "state", "host_keys", "trusted tls"):
        (root / directory).mkdir(mode=0o700)
    shutil.copyfile(tls / "ca.pem", root / "trusted tls" / "ca.pem")
    model = compose_model(config, root, allow_local_build=False)
    model["name"] = server.name
    model["networks"] = {"default": {"external": True, "name": network}}
    service = model["services"]["sftpwarden"]
    service["ports"] = ["127.0.0.1::22"]
    service["healthcheck"]["interval"] = "1s"
    service["volumes"].append(
        {
            "type": "bind",
            "source": str(root / "trusted tls"),
            "target": "/trusted-tls",
            "read_only": True,
        }
    )
    compose_path = root / "compose.yaml"
    compose_path.write_text(yaml.safe_dump(model))
    command = ["docker", "compose", "-f", str(compose_path)]
    runtime_dsn = (
        f"{case.provider}://sftpwarden:{password}@database/sftpwarden?ssl_ca=/trusted-tls/ca.pem"
    )
    runtime_environment = {
        **os.environ,
        "DB_URL": runtime_dsn,
        "UNRELATED_SECRET": "must-not-forward",
    }
    cleanup_stack.add(
        lambda: run_external(
            [*command, "down", "--volumes"],
            env=runtime_environment,
            timeout=60,
        )
    )
    missing_env = {key: value for key, value in os.environ.items() if key != "DB_URL"}
    missing = run_external([*command, "config", "--quiet"], env=missing_env, check=False)
    assert missing.returncode != 0
    assert "Required provider environment variable is missing" in missing.stderr
    started = run_external(
        [*command, "up", "-d", "--wait", "--wait-timeout", "90"],
        env=runtime_environment,
        timeout=120,
    )
    assert "transport-sentinel" not in started.stdout + started.stderr + compose_path.read_text()
    result = run_external(
        [
            "docker",
            "exec",
            server.name,
            "python",
            "-c",
            "import os; assert 'UNRELATED_SECRET' not in os.environ; "
            "assert '$literal;&%' in os.environ['DB_URL'] or '%24literal' in os.environ['DB_URL']",
        ]
    )
    assert result.returncode == 0
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
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    users.users[0].disabled = True
    provider.write(users)
    eventually(
        lambda: "Permission denied (publickey)" in server.connect("alice").stderr,
        timeout_seconds=20,
        description="database revocation over verified TLS",
    )
    assert server.connect("control").returncode == 0


@pytest.mark.parametrize("kind", ["mysql", "mongodb", "postgresql"])
def test_provider_stalled_socket_has_finite_timeout(kind: str) -> None:
    """A listening server that never speaks must not hang the CLI driver indefinitely."""
    stopped = threading.Event()
    connections = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)

        def accept() -> None:
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                    connections.append(connection)
                except TimeoutError:
                    continue

        worker = threading.Thread(target=accept, daemon=True)
        worker.start()
        try:
            port = listener.getsockname()[1]
            if kind == "mysql":
                provider = MySQLProvider(
                    ProviderConfig(
                        type="mysql",
                        dsn=f"mysql://u:p@127.0.0.1:{port}/test?read_timeout=1",
                    )
                )
            elif kind == "postgresql":
                provider = PostgreSQLProvider(
                    ProviderConfig(
                        type="postgresql",
                        dsn=f"postgresql://u:p@127.0.0.1:{port}/test?connect_timeout=1",
                    )
                )
            else:
                provider = MongoDBProvider(
                    ProviderConfig(
                        type="mongodb",
                        dsn=f"mongodb://127.0.0.1:{port}/test?"
                        "connectTimeoutMS=300&serverSelectionTimeoutMS=600&socketTimeoutMS=300&timeoutMS=1000",
                    )
                )
            started = time.monotonic()
            with pytest.raises(ProviderError, match="timed out"):
                provider.read()
            assert time.monotonic() - started < 4
            assert connections
        finally:
            stopped.set()
            worker.join(timeout=2)
            for connection in connections:
                connection.close()
            assert not worker.is_alive()


@pytest.mark.release_docker
@pytest.mark.parametrize(
    "case", [DATABASE_CASES[0], DATABASE_CASES[-1]], ids=["postgresql", "mongodb"]
)
def test_postgres_and_mongo_verified_tls_reach_runtime(
    case,
    policy_image,
    cleanup_stack,
    tmp_path,
):
    """Verify CA and hostname failures with a live positive control, then real SFTP."""
    root = tmp_path / "tls-provider"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    tls = root / "private tls"
    create_tls_material(tls)
    if case.provider == "mongodb":
        combined = tls / "combined.pem"
        combined.write_bytes((tls / "server.key").read_bytes() + (tls / "server.pem").read_bytes())
        combined.chmod(0o600)
    name = f"sftpwarden-tls-{uuid4().hex[:12]}"
    network = name + "-net"
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network], timeout=60))
    run_external(["docker", "network", "create", network])
    existed = run_external(["docker", "image", "inspect", case.image], check=False).returncode == 0
    if not existed:
        cleanup_stack.add(lambda: run_external(["docker", "image", "rm", case.image], timeout=120))
        run_external(["docker", "pull", case.image], timeout=300)
    metadata = json.loads(run_external(["docker", "image", "inspect", case.image]).stdout)[0]
    entrypoint = metadata["Config"]["Entrypoint"]
    assert len(entrypoint) == 1
    tls_arguments = (
        [
            "-c",
            "ssl=on",
            "-c",
            "ssl_cert_file=/tmp/tls/server.pem",
            "-c",
            "ssl_key_file=/tmp/tls/server.key",
        ]
        if case.provider == "postgresql"
        else [
            "--tlsMode",
            "requireTLS",
            "--tlsCertificateKeyFile",
            "/tmp/tls/combined.pem",  # noqa: S108 - isolated disposable container
            "--tlsCAFile",
            "/tmp/tls/ca.pem",  # noqa: S108 - isolated disposable container
            "--tlsAllowConnectionsWithoutCertificates",
        ]
    )
    cleanup_stack.add(lambda: remove_docker_container(name))
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        "database",
        "-p",
        f"127.0.0.1::{case.internal_port}",
        "--entrypoint",
        "/bin/sh",
    ]
    for variable in case.environment:
        command.extend(["--env", variable])
    command.extend(
        [
            case.image,
            "-ec",
            'while [ ! -f /tmp/tls-ready ]; do sleep 0.1; done; exec "$@"',
            "sh",
            entrypoint[0],
            *metadata["Config"]["Cmd"],
            *tls_arguments,
        ]
    )
    run_external(command, env=dict(os.environ, **case.environment))
    run_external(
        ["docker", "exec", name, "mkdir", "-m", "700", "/tmp/tls"]  # noqa: S108
    )
    files = (
        ("ca.pem", "server.pem", "server.key")
        if case.provider == "postgresql"
        else ("ca.pem", "combined.pem")
    )
    for filename in files:
        run_external(["docker", "cp", str(tls / filename), f"{name}:/tmp/tls/{filename}"])
    owner = "postgres:postgres" if case.provider == "postgresql" else "mongodb:mongodb"
    run_external(["docker", "exec", name, "chown", "-R", owner, "/tmp/tls"])  # noqa: S108
    run_external(["docker", "exec", name, "touch", "/tmp/tls-ready"])  # noqa: S108
    port = int(
        run_external(["docker", "port", name, f"{case.internal_port}/tcp"])
        .stdout.strip()
        .rsplit(":", 1)[1]
    )
    base = (
        case.dsn_template.format(port=port)
        .replace("@127.0.0.1:", "@localhost:")
        .replace("//127.0.0.1:", "//localhost:")
    )

    def tls_dsn(ca, *, host_base=base):
        options = (
            "sslmode=verify-full&sslrootcert="
            if case.provider == "postgresql"
            else "tls=true&tlsCAFile="
        )
        return host_base + "?" + options + quote(str(ca), safe="")

    provider = provider_for_config(
        ProviderConfig(type=case.provider, dsn=tls_dsn(tls / "ca.pem"), user_schema=2)
    )

    def ready():
        state = json.loads(
            run_external(["docker", "inspect", "--format", "{{json .State}}", name]).stdout
        )
        assert state["Running"], "Database exited before TLS readiness"
        try:
            provider.create_table()
            return provider.read().users == []
        except ProviderError:
            return False

    eventually(ready, timeout_seconds=180, description="verified TLS provider readiness")
    for bad in (
        tls_dsn(tls / "untrusted.pem"),
        tls_dsn(tls / "ca.pem", host_base=base.replace("localhost", "127.0.0.1")),
    ):
        with pytest.raises(ProviderError):
            provider_for_config(ProviderConfig(type=case.provider, dsn=bad, user_schema=2)).read()
        assert provider.read().users == []
    server = PolicyRuntime(root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    for username, key in (("alice", server.alice_public), ("control", server.control_public)):
        provider.upsert_user(
            SFTPUser(username=username, keys=[SFTPUserKey(name=username, public_key=key)])
        )
    runtime_config = yaml.safe_load((server.inputs / "config.yaml").read_text())
    runtime_base = base.replace(f"localhost:{port}", f"database:{case.internal_port}")
    runtime_config["provider"] = {
        "type": case.provider,
        "dsn": tls_dsn("/trusted/ca.pem", host_base=runtime_base),
        "user_schema": 2,
    }
    (server.inputs / "config.yaml").write_text(yaml.safe_dump(runtime_config))
    server.start(
        docker_args=(
            "--network",
            network,
            "--mount",
            f"type=bind,src={tls / 'ca.pem'},dst=/trusted/ca.pem,readonly",
        )
    )
    assert server.connect("alice").returncode == 0
    expected = provider.read()
    alice = next(user for user in expected.users if user.username == "alice")
    provider.mutate_user(expected, "alice", alice.model_copy(update={"disabled": True}))
    eventually(
        lambda: "Permission denied (publickey)" in server.connect("alice").stderr,
        timeout_seconds=30,
        description="revocation over verified provider TLS",
    )
    server.assert_denied()
    if case.provider == "postgresql":
        import psycopg

        from sftpwarden.providers.postgres_transport import connect_postgres

        connection = connect_postgres(psycopg, tls_dsn(tls / "ca.pem"))
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                connection.execute("select pg_sleep(60)")
            assert 19 <= time.monotonic() - started < 25
            assert connection.closed
        finally:
            connection.close()
    else:
        before = json.loads(
            run_external(
                [
                    "docker",
                    "exec",
                    server.name,
                    "cat",
                    "/var/lib/sftpwarden/state.json",
                ]
            ).stdout
        )
        database = provider._database()
        try:
            database.drop_collection(provider.config.collection)
        finally:
            database.client.close()
        with pytest.raises(ProviderError, match="missing"):
            provider.read()
        eventually(
            lambda: (
                run_external(
                    [
                        "docker",
                        "exec",
                        server.name,
                        "sftpwarden",
                        "runtime",
                        "health",
                        "--config",
                        "/inputs/config.yaml",
                        "--json",
                    ],
                    check=False,
                ).returncode
                != 0
            ),
            timeout_seconds=30,
            description="missing MongoDB collection reported without empty-policy application",
        )
        after = json.loads(
            run_external(
                [
                    "docker",
                    "exec",
                    server.name,
                    "cat",
                    "/var/lib/sftpwarden/state.json",
                ]
            ).stdout
        )
        assert after["fingerprint"] == before["fingerprint"]
        assert server.connect("control").returncode == 0
