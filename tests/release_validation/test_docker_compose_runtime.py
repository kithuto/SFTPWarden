from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import (
    TEST_HASH,
    CleanupStack,
    ReleaseCli,
    assert_ok,
    docker_compose_down,
    docker_image_id,
    eventually,
    free_tcp_port,
    preserve_or_remove_image,
    remove_docker_container,
    require_executable,
    run_external,
)


def generate_keypair(path: Path) -> tuple[Path, str]:
    """Generate a real OpenSSH keypair for an SFTP round trip."""
    require_executable("ssh-keygen")
    run_external(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(path),
            "-C",
            "sftpwarden-release-validation",
        ],
        timeout=60,
    )
    return path, path.with_suffix(".pub").read_text(encoding="utf-8").strip()


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_docker
def test_docker_compose_deploy_refresh_health_and_sftp_round_trip(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
) -> None:
    """Run the real OpenSSH runtime through Docker Compose and upload a file over SFTP."""
    require_executable("docker")
    require_executable("sftp")
    run_external(["docker", "version"], timeout=60)
    run_external(["docker", "compose", "version"], timeout=60)

    previous_runtime_image = docker_image_id("sftpwarden:local")
    root = tmp_path / "compose-runtime"
    container_name = f"sftpwarden-{unique_name}"
    port = free_tcp_port()
    cleanup_stack.add(lambda: preserve_or_remove_image("sftpwarden:local", previous_runtime_image))
    cleanup_stack.add(lambda: docker_compose_down(root))
    cleanup_stack.add(lambda: remove_docker_container(container_name))

    private_key, public_key = generate_keypair(tmp_path / "alice_ed25519")
    known_hosts = tmp_path / "known_hosts"
    local_payload = tmp_path / "payload.txt"
    batch_file = tmp_path / "sftp.batch"
    local_payload.write_text("hello from release validation\n", encoding="utf-8")
    batch_file.write_text(
        f"put {local_payload.as_posix()} upload/payload.txt\nls upload\n", encoding="utf-8"
    )

    assert_ok(cli.run("init", "compose", "--root", root, "--yes"))
    for path, value in {
        "docker.container_name": container_name,
        "server.port": str(port),
        "healthcheck.interval_seconds": "5",
        "healthcheck.timeout_seconds": "3",
        "healthcheck.retries": "18",
        "healthcheck.start_period_seconds": "5",
        "sync.interval_seconds": "5",
    }.items():
        assert_ok(cli.run("config", path, value, "--context", "compose"))
    assert_ok(
        cli.run(
            "user",
            "create",
            "alice",
            "--public-key",
            public_key,
            "--context",
            "compose",
            "--no-refresh",
        )
    )

    dry_run = cli.run("deploy", "--context", "compose", "--dry-run", "--json")
    assert_ok(dry_run)
    assert json.loads(dry_run.stdout)["plan"]["target"] == "compose"

    assert_ok(cli.run("deploy", "--context", "compose", "--yes", timeout=900))

    def container_is_healthy() -> bool:
        result = run_external(
            ["docker", "inspect", container_name, "--format", "{{.State.Health.Status}}"],
            check=False,
            timeout=30,
        )
        return result.returncode == 0 and result.stdout.strip() == "healthy"

    eventually(
        container_is_healthy,
        timeout_seconds=180,
        interval_seconds=3,
        description=f"Docker container {container_name} to become healthy",
    )

    health = cli.run("health", "--context", "compose", "--json", timeout=120)
    assert_ok(health)
    assert json.loads(health.stdout)["healthy"]
    run_external(
        [
            "docker",
            "exec",
            container_name,
            "sftpwarden",
            "runtime",
            "health",
            "--json",
        ],
        timeout=60,
    )

    def sftp_upload_succeeds() -> bool:
        result = run_external(
            [
                "sftp",
                "-b",
                str(batch_file),
                "-i",
                str(private_key),
                "-P",
                str(port),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                f"UserKnownHostsFile={known_hosts}",
                "alice@127.0.0.1",
            ],
            check=False,
            timeout=60,
        )
        return result.returncode == 0

    eventually(
        sftp_upload_succeeds,
        timeout_seconds=120,
        interval_seconds=3,
        description="SFTP upload to the Docker runtime",
    )
    assert (root / "data" / "alice" / "upload" / "payload.txt").read_text(
        encoding="utf-8"
    ) == "hello from release validation\n"

    assert_ok(
        cli.run(
            "user",
            "create",
            "bob",
            "--password-hash",
            TEST_HASH,
            "--context",
            "compose",
            "--no-refresh",
        )
    )
    refresh = cli.run("refresh", "--context", "compose", "--json", timeout=120)
    assert_ok(refresh)
    assert json.loads(refresh.stdout)["targets"][0]["context"] == "compose"
    run_external(["docker", "exec", container_name, "id", "bob"], timeout=60)
