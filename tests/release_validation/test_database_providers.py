from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from .conftest import (
    FAKE_KEY_1,
    TEST_HASH,
    CleanupStack,
    CliResult,
    ReleaseCli,
    assert_failed,
    assert_ok,
    free_tcp_port,
    require_executable,
    run_external,
)


@dataclass(frozen=True)
class DatabaseCase:
    """Real database provider container configuration."""

    provider: str
    image: str
    internal_port: int
    environment: dict[str, str]
    dsn_template: str


DATABASE_CASES = [
    DatabaseCase(
        provider="postgresql",
        image="postgres:16-alpine",
        internal_port=5432,
        environment={
            "POSTGRES_DB": "sftpwarden",
            "POSTGRES_USER": "sftpwarden",
            "POSTGRES_PASSWORD": "sftpwarden",
        },
        dsn_template="postgresql://sftpwarden:sftpwarden@127.0.0.1:{port}/sftpwarden",
    ),
    DatabaseCase(
        provider="mysql",
        image="mysql:8",
        internal_port=3306,
        environment={
            "MYSQL_ROOT_PASSWORD": "sftpwarden-root",
            "MYSQL_DATABASE": "sftpwarden",
            "MYSQL_USER": "sftpwarden",
            "MYSQL_PASSWORD": "sftpwarden",
        },
        dsn_template="mysql://sftpwarden:sftpwarden@127.0.0.1:{port}/sftpwarden",
    ),
    DatabaseCase(
        provider="mariadb",
        image="mariadb:11",
        internal_port=3306,
        environment={
            "MARIADB_ROOT_PASSWORD": "sftpwarden-root",
            "MARIADB_DATABASE": "sftpwarden",
            "MARIADB_USER": "sftpwarden",
            "MARIADB_PASSWORD": "sftpwarden",
        },
        dsn_template="mariadb://sftpwarden:sftpwarden@127.0.0.1:{port}/sftpwarden",
    ),
    DatabaseCase(
        provider="mongodb",
        image="mongo:7",
        internal_port=27017,
        environment={},
        dsn_template="mongodb://127.0.0.1:{port}/sftpwarden",
    ),
]


def start_database_container(
    case: DatabaseCase,
    *,
    name: str,
    host_port: int,
    cleanup_stack: CleanupStack,
) -> None:
    """Start one provider database in Docker and register cleanup."""
    image_existed = (
        run_external(
            ["docker", "image", "inspect", case.image],
            check=False,
            timeout=30,
        ).returncode
        == 0
    )
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{host_port}:{case.internal_port}",
    ]
    for key, value in case.environment.items():
        command.extend(["-e", f"{key}={value}"])
    command.append(case.image)
    if not image_existed:
        cleanup_stack.add(
            lambda: run_external(["docker", "image", "rm", case.image], check=False, timeout=120)
        )
    run_external(command, timeout=300)
    cleanup_stack.add(lambda: run_external(["docker", "rm", "-f", name], check=False, timeout=60))


def init_when_database_is_ready(
    cli: ReleaseCli,
    *,
    case: DatabaseCase,
    root: Path,
    dsn: str,
    timeout_seconds: int = 180,
) -> CliResult:
    """Retry init until the database accepts connections, failing fast on missing extras."""
    deadline = time.monotonic() + timeout_seconds
    last: CliResult | None = None
    while time.monotonic() < deadline:
        last = cli.run(
            "init",
            case.provider,
            "--root",
            root,
            "--provider",
            case.provider,
            "--dsn",
            dsn,
            "--create-table",
            "--yes",
            timeout=90,
        )
        if last.returncode == 0:
            return last
        if "optional dependency" in last.output:
            pytest.fail(
                f"{case.provider} release validation requires the optional provider extra.\n"
                f"{last.output}"
            )
        time.sleep(3)
    pytest.fail(
        f"Timed out waiting for {case.provider} database to initialize.\n"
        f"{last.output if last else ''}"
    )


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_docker
@pytest.mark.release_databases
@pytest.mark.parametrize("case", DATABASE_CASES, ids=[case.provider for case in DATABASE_CASES])
def test_real_database_provider_crud_import_export_and_backup(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
    case: DatabaseCase,
) -> None:
    """Run provider commands against real SQL/MongoDB containers."""
    require_executable("docker")
    run_external(["docker", "version"], timeout=60)

    host_port = free_tcp_port()
    container_name = f"sftpwarden-{case.provider}-{unique_name}"
    start_database_container(
        case, name=container_name, host_port=host_port, cleanup_stack=cleanup_stack
    )
    dsn = case.dsn_template.format(port=host_port)
    root = tmp_path / f"{case.provider}-project"
    export_path = tmp_path / f"{case.provider}-users.json"
    backup_path = tmp_path / f"{case.provider}-backup.tar.gz"

    assert_ok(init_when_database_is_ready(cli, case=case, root=root, dsn=dsn))
    assert_ok(
        cli.run(
            "user",
            "create",
            "alice",
            "--password-hash",
            TEST_HASH,
            "--context",
            case.provider,
            "--no-refresh",
        )
    )
    assert_ok(
        cli.run(
            "user",
            "key",
            "add",
            "alice",
            "prod-ci",
            "--public-key",
            FAKE_KEY_1,
            "--context",
            case.provider,
            "--no-refresh",
        )
    )

    users = cli.run("users", "--context", case.provider, "--json")
    schema = cli.run("provider", "schema", "show", "--context", case.provider, "--json")
    assert_ok(users)
    assert_ok(schema)
    assert json.loads(users.stdout)["users"][0]["username"] == "alice"
    assert json.loads(schema.stdout)["provider_user_schema"] == 2

    assert_ok(
        cli.run(
            "provider",
            "export",
            "--context",
            case.provider,
            "--format",
            "json",
            "--output",
            export_path,
        )
    )
    assert json.loads(export_path.read_text(encoding="utf-8"))["users"][0]["username"] == "alice"

    assert_ok(
        cli.run("user", "remove", "alice", "--context", case.provider, "--yes", "--no-refresh")
    )
    imported = cli.run(
        "provider",
        "import",
        "--context",
        case.provider,
        "--input",
        export_path,
        "--replace",
        "--json",
        "--no-refresh",
    )
    assert_ok(imported)
    assert json.loads(imported.stdout)["destination_count"] == 1

    assert_ok(cli.run("backup", "--context", case.provider, "--output", backup_path, "--yes"))
    assert backup_path.exists()


@pytest.mark.release_validation
def test_external_database_init_without_dsn_is_actionable(cli: ReleaseCli, tmp_path: Path) -> None:
    """Non-interactive database init must not silently promise impossible setup."""
    assert_failed(
        cli.run(
            "init",
            "pg",
            "--provider",
            "postgresql",
            "--root",
            tmp_path / "pg",
            "--yes",
        ),
        "postgresql provider requires --dsn.",
        "Pass a database URL with --dsn",
    )
