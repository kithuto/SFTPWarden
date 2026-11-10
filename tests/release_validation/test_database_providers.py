from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from .conftest import (
    PUBLIC_KEY_1,
    TEST_HASH,
    CleanupStack,
    CliResult,
    ReleaseCli,
    assert_failed,
    assert_ok,
    remove_docker_container,
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
        image=(  # 16.15 / Alpine 3.24
            "postgres:16-alpine@sha256:"
            "aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139"
        ),
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
        image=(  # 8.4.13
            "mysql:8@sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902"
        ),
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
        image=(  # 11.8.9
            "mariadb:11@sha256:c4124d1afa875e47f2892edf26b482fa54f796e2f342d3717a04ca091a22a9bd"
        ),
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
        image=(  # 7.0.45
            "mongo:7@sha256:8bb4442761a63a8dda452744082cdac9644b429807eb977b42324a63cef59ca4"
        ),
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
    arguments: tuple[str, ...] = (),
) -> int:
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
    command.extend(arguments)
    if not image_existed:
        cleanup_stack.add(
            lambda: run_external(["docker", "image", "rm", case.image], check=False, timeout=120)
        )
    cleanup_stack.add(lambda: remove_docker_container(name))
    run_external(command, timeout=300)
    return int(
        run_external(["docker", "port", name, f"{case.internal_port}/tcp"])
        .stdout.strip()
        .rsplit(":", 1)[1]
    )


def init_when_database_is_ready(
    cli: ReleaseCli,
    *,
    case: DatabaseCase,
    root: Path,
    dsn: str,
    timeout_seconds: int = 180,
    user_schema: int = 2,
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
            "--user-schema",
            str(user_schema),
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

    container_name = f"sftpwarden-{case.provider}-{unique_name}"
    host_port = start_database_container(
        case, name=container_name, host_port=0, cleanup_stack=cleanup_stack
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
            PUBLIC_KEY_1,
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
    if case.provider == "mongodb":
        assert_failed(imported, "requires a replica set")
        assert_ok(
            cli.run(
                "user",
                "create",
                "alice",
                "--public-key",
                PUBLIC_KEY_1,
                "--context",
                case.provider,
                "--no-refresh",
            )
        )
    else:
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
