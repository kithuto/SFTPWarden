from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import load_config, provider_local_path
from sftpwarden.providers import provider_for_config
from sftpwarden.users import SFTPUser

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


def publish_fixed_test_image(
    cleanup_stack: CleanupStack, unique_name: str, runtime_container: str
) -> str:
    """Serve one fixed artifact from an owned loopback registry for repeated CLI deploys."""
    registry = f"sftpwarden-registry-{unique_name}"
    previous = docker_image_id("registry:2")
    if previous is None:
        cleanup_stack.add(lambda: run_external(["docker", "image", "rm", "registry:2"]))
    cleanup_stack.add(lambda: remove_docker_container(registry))
    run_external(
        [
            "docker",
            "run",
            "-d",
            "--name",
            registry,
            "--network",
            "host",
            "-e",
            "REGISTRY_HTTP_ADDR=127.0.0.1:0",
            "registry:2",
        ],
        timeout=180,
    )
    port = ""

    def registry_ready() -> bool:
        nonlocal port
        result = run_external(["docker", "logs", registry], check=False)
        match = re.search(r"listening on 127\.0\.0\.1:(\d+)", result.stdout + result.stderr)
        if match is None or match[1] == "0":
            return False
        port = match[1]
        return (
            run_external(
                ["docker", "exec", registry, "wget", "-qO-", f"http://127.0.0.1:{port}/v2/"],
                check=False,
            ).returncode
            == 0
        )

    eventually(
        registry_ready,
        timeout_seconds=30,
        description="owned registry on the Docker daemon's loopback interface",
    )
    image = f"127.0.0.1:{port}/sftpwarden:fixed"
    cleanup_stack.add(lambda: run_external(["docker", "image", "rm", image]))
    cleanup_stack.add(lambda: remove_docker_container(runtime_container))
    run_external(["docker", "tag", "sftpwarden:local", image])
    run_external(["docker", "push", image], timeout=180)
    return image


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_docker
@pytest.mark.parametrize("bind_host", ["127.0.0.1", "::1"])
@pytest.mark.parametrize("provider_kind", ["yaml", "csv", "sqlite"])
def test_docker_compose_deploy_refresh_health_and_sftp_round_trip(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
    bind_host: str,
    provider_kind: str,
) -> None:
    """Run the real OpenSSH runtime through Docker Compose and upload a file over SFTP."""
    require_executable("docker")
    require_executable("sftp")
    run_external(["docker", "version"], timeout=60)
    run_external(["docker", "compose", "version"], timeout=60)

    previous_runtime_image = docker_image_id("sftpwarden:local")
    candidate = os.environ.get("SFTPWARDEN_RELEASE_RUNTIME_IMAGE")
    root = tmp_path / "compose-runtime"
    container_name = f"sftpwarden-{unique_name}"
    port = free_tcp_port()
    cleanup_stack.add(
        lambda: preserve_or_remove_image(
            "sftpwarden:local", previous_runtime_image, created_image=not candidate
        )
    )
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    cleanup_stack.add(lambda: docker_compose_down(root))
    cleanup_stack.add(lambda: remove_docker_container(container_name))

    for identity in (tmp_path / "alice_ed25519", tmp_path / "charlie_ed25519"):
        cleanup_stack.add(lambda identity=identity: identity.unlink(missing_ok=True))
        cleanup_stack.add(
            lambda identity=identity: identity.with_suffix(".pub").unlink(missing_ok=True)
        )
    private_key, public_key = generate_keypair(tmp_path / "alice_ed25519")
    charlie_key, charlie_public = generate_keypair(tmp_path / "charlie_ed25519")
    known_hosts = tmp_path / "known_hosts"
    local_payload = tmp_path / "payload.txt"
    batch_file = tmp_path / "sftp.batch"
    local_payload.write_text("hello from release validation\n", encoding="utf-8")
    batch_file.write_text(
        f'put "{local_payload.as_posix()}" upload/payload.txt\n'
        f'get upload/payload.txt "{(tmp_path / "download.txt").as_posix()}"\nls upload\n',
        encoding="utf-8",
    )

    assert_ok(cli.run("init", "compose", "--root", root, "--provider", provider_kind, "--yes"))
    fixed_image = None
    if candidate:
        run_external(["docker", "tag", candidate, "sftpwarden:local"])
        fixed_image = publish_fixed_test_image(cleanup_stack, unique_name, container_name)
    config_values = {
        "docker.container_name": container_name,
        "server.port": str(port),
        "server.host": bind_host,
        "healthcheck.interval_seconds": "5",
        "healthcheck.timeout_seconds": "3",
        "healthcheck.retries": "18",
        "healthcheck.start_period_seconds": "5",
        "sync.interval_seconds": "5",
    }
    if fixed_image:
        config_values["docker.image"] = fixed_image
    if provider_kind == "yaml" and bind_host == "127.0.0.1":
        config_values.update(
            {
                "server.data_dir": "/srv/sftp/data",
                "server.state_dir": "/srv/sftp/state",
                "server.host_keys_dir": "/srv/sftp/identity",
            }
        )
    for path, value in config_values.items():
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

    files_before_plan = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    dry_run = cli.run("deploy", "--context", "compose", "--dry-run", "--json")
    assert_ok(dry_run)
    assert json.loads(dry_run.stdout)["plan"]["target"] == "compose"
    assert files_before_plan == {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }

    assert_ok(cli.run("deploy", "--context", "compose", "--yes", timeout=900))
    migrated = load_config(root / "sftpwarden.yaml")
    provider_path = provider_local_path(root, migrated)
    assert provider_path.parent == root / "provider"
    assert (root / provider_path.name).exists(), "The old provider must remain recoverable"

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

    updated_port = free_tcp_port()
    config_path = root / "sftpwarden.yaml"
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_data["server"]["port"] = updated_port
    config_data["healthcheck"]["timeout_seconds"] = 4
    config_data["docker"]["restart"] = "always"
    config_path.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")

    assert_ok(cli.run("deploy", "--context", "compose", "--yes", timeout=900))
    compose_data = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose_data["services"]["sftpwarden"]
    assert service["ports"] == [
        {"target": 22, "published": str(updated_port), "host_ip": bind_host, "protocol": "tcp"}
    ]
    assert service["healthcheck"]["timeout"] == "4s"
    assert service["restart"] == "always"
    restart_policy = run_external(
        [
            "docker",
            "inspect",
            container_name,
            "--format",
            "{{.HostConfig.RestartPolicy.Name}}",
        ],
        timeout=30,
    )
    published_port = run_external(["docker", "port", container_name, "22/tcp"], timeout=30)
    assert restart_policy.stdout.strip() == "always"
    assert f":{updated_port}" in published_port.stdout
    address = f"[{bind_host}]" if ":" in bind_host else bind_host
    assert published_port.stdout.strip() == f"{address}:{updated_port}"
    port = updated_port

    eventually(
        container_is_healthy,
        timeout_seconds=180,
        interval_seconds=3,
        description=f"Docker container {container_name} to become healthy after config deploy",
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

    host_key = run_external(
        [
            "docker",
            "exec",
            container_name,
            "cat",
            f"{migrated.server.host_keys_dir}/ssh_host_ed25519_key.pub",
        ],
        timeout=30,
    ).stdout.strip()
    known_hosts.write_text(f"[{bind_host}]:{port} {host_key}\n", encoding="utf-8")

    def sftp_upload_succeeds(username: str = "alice", identity: Path = private_key) -> bool:
        result = run_external(
            [
                "sftp",
                "-F",
                os.devnull,
                "-b",
                str(batch_file),
                "-i",
                str(identity),
                "-P",
                str(port),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "IdentityAgent=none",
                "-o",
                "ControlMaster=no",
                "-o",
                "ControlPath=none",
                "-o",
                f"UserKnownHostsFile={known_hosts}",
                f"{username}@{address}",
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
    assert (tmp_path / "download.txt").read_text(encoding="utf-8") == (
        "hello from release validation\n"
    )

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
    container_id = run_external(["docker", "inspect", "-f", "{{.Id}}", container_name]).stdout
    # An external editor publishes a new inode. The running directory mount must see it.
    external = root / f"external.{provider_kind}"
    editor = provider_for_config(migrated.provider, external)
    users = provider_for_config(migrated.provider, provider_path).read()
    users.users.append(SFTPUser(username="charlie", public_keys=[charlie_public]))
    editor.write(users)
    external.replace(provider_path)
    assert_ok(cli.run("refresh", "--context", "compose", "--json", timeout=120))
    run_external(["docker", "exec", container_name, "id", "charlie"], timeout=60)
    assert (
        run_external(["docker", "inspect", "-f", "{{.Id}}", container_name]).stdout == container_id
    )
    assert sftp_upload_succeeds()
    assert sftp_upload_succeeds("charlie", charlie_key)
    if provider_kind == "yaml" and bind_host == "127.0.0.1":
        # Source deploys invoke a build. Pin an actual registry artifact to prove
        # the next config-only deploy also recreates an unchanged image.
        fixed_image = fixed_image or publish_fixed_test_image(
            cleanup_stack, unique_name, container_name
        )
        desired = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        desired["docker"]["image"] = fixed_image
        config_path.write_text(yaml.safe_dump(desired), encoding="utf-8")
        assert_ok(cli.run("deploy", "--context", "compose", "--yes", timeout=900))
        container_id = run_external(["docker", "inspect", "-f", "{{.Id}}", container_name]).stdout
        # Daemon config changes alone do not alter the image or Compose service.
        image_before = run_external(
            ["docker", "inspect", "-f", "{{.Image}}", container_name]
        ).stdout
        daemon_before = run_external(
            ["docker", "exec", container_name, "cat", "/etc/ssh/sshd_config"]
        ).stdout
        assert_ok(cli.run("config", "auth.allow_password", "false", "--context", "compose"))
        pending = cli.run("refresh", "--context", "compose", "--json", timeout=60)
        assert_ok(pending)
        assert "Pending configuration changes require" in pending.stdout
        assert (
            run_external(["docker", "exec", container_name, "cat", "/etc/ssh/sshd_config"]).stdout
            == daemon_before
        )
        assert sftp_upload_succeeds()
        assert_ok(
            cli.run(
                "config",
                "provider.path",
                "/etc/sftpwarden/provider-data/not-deployed.yaml",
                "--context",
                "compose",
            )
        )
        rejected = cli.run("refresh", "--context", "compose", "--json", timeout=60)
        assert rejected.returncode != 0
        assert "Refresh failed" in rejected.output and "sftpwarden deploy" in rejected.output
        assert sftp_upload_succeeds()
        assert_ok(
            cli.run("config", "provider.path", migrated.provider.path, "--context", "compose")
        )
        assert_ok(cli.run("deploy", "--context", "compose", "--yes", timeout=900))
        assert (
            run_external(["docker", "inspect", "-f", "{{.Id}}", container_name]).stdout
            != container_id
        )
        assert (
            run_external(["docker", "inspect", "-f", "{{.Image}}", container_name]).stdout
            == image_before
        )
        effective = run_external(["docker", "exec", container_name, "sshd", "-T"]).stdout
        assert "passwordauthentication no" in effective
        assert sftp_upload_succeeds()
