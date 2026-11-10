from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sftpwarden.services.kubernetes_identity import idle_helper_command

from .conftest import (
    CleanupStack,
    ReleaseCli,
    assert_ok,
    eventually,
    remove_docker_container,
    require_executable,
    run_external,
)
from .test_auth_policy import policy_image as policy_image

COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


def _contexts(result) -> dict:
    return json.loads(result.stdout)["contexts"]


@pytest.mark.release_validation
def test_context_remove_command_removes_local_project_root_and_registry_entry(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """`context remove` should clean the named local project without touching other contexts."""
    stale_root = tmp_path / "remove-me"
    live_root = tmp_path / "keep-me"

    assert_ok(cli.run("init", "stale", "--root", stale_root, "--yes"))
    assert_ok(cli.run("init", "live", "--root", live_root, "--yes"))

    removed = cli.run("context", "remove", "stale", "--yes")
    listed = cli.run("context", "ls", "--json")

    assert_ok(removed)
    assert_ok(listed)
    assert not stale_root.exists()
    assert live_root.exists()
    assert set(_contexts(listed)) == {"live"}


@pytest.mark.release_validation
def test_manual_remote_local_sync_folder_deletion_prunes_only_local_state(
    cli: ReleaseCli,
    tmp_path: Path,
) -> None:
    """Manual deletion of a remote local-sync folder must not SSH into the remote host."""
    stale_root = tmp_path / "deleted-remote-local"
    live_root = tmp_path / "keep-local"

    assert_ok(cli.run("init", "live", "--root", live_root, "--yes"))
    assert_ok(
        cli.run(
            "init",
            "stale-remote",
            "--remote",
            "deploy@unreachable.invalid:/srv/sftpwarden-stale",
            "--root",
            stale_root,
            "--provider",
            "yaml",
            "--watcher",
            "systemd",
            "--critical",
            "--skip-checks",
            "--yes",
        )
    )
    shutil.rmtree(stale_root)

    listed = cli.run("context", "ls", "--json", timeout=20)
    watcher = cli.run("watcher", "status", "--json")

    assert_ok(listed)
    assert_ok(watcher)
    assert set(_contexts(listed)) == {"live"}
    assert json.loads(watcher.stdout)["installed"] is False


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_docker
def test_manual_local_folder_deletion_removes_real_orphaned_docker_resources(
    cli: ReleaseCli,
    cleanup_stack: CleanupStack,
    tmp_path: Path,
    unique_name: str,
) -> None:
    """Manual deletion should remove Docker resources labelled for the missing project root."""
    require_executable("docker")
    run_external(["docker", "version"], timeout=60)

    stale_root = tmp_path / "deleted-local"
    live_root = tmp_path / "keep-local"
    image = f"sftpwarden-context-cleanup-{unique_name}:latest"
    container = f"sftpwarden-context-cleanup-{unique_name}"
    network = f"sftpwarden-context-cleanup-net-{unique_name}"
    volume = f"sftpwarden-context-cleanup-vol-{unique_name}"

    dockerfile_root = tmp_path / "scratch-image"
    dockerfile_root.mkdir()
    (dockerfile_root / "Dockerfile").write_text(
        'FROM scratch\nCMD ["/bin/false"]\n',
        encoding="utf-8",
    )

    assert_ok(cli.run("init", "stale", "--root", stale_root, "--yes"))
    assert_ok(cli.run("init", "live", "--root", live_root, "--yes"))
    label = f"{COMPOSE_WORKING_DIR_LABEL}={stale_root.resolve(strict=False)}"

    cleanup_stack.add(lambda: run_external(["docker", "image", "rm", "-f", image], check=False))
    cleanup_stack.add(lambda: remove_docker_container(container))
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network], check=False))
    cleanup_stack.add(lambda: run_external(["docker", "volume", "rm", "-f", volume], check=False))
    run_external(["docker", "build", "-t", image, "."], cwd=dockerfile_root, timeout=120)
    run_external(["docker", "create", "--name", container, "--label", label, image], timeout=60)
    run_external(["docker", "network", "create", "--label", label, network], timeout=60)
    run_external(["docker", "volume", "create", "--label", label, volume], timeout=60)

    shutil.rmtree(stale_root)

    listed = cli.run("context", "ls", "--json", timeout=60)

    assert_ok(listed)
    assert set(_contexts(listed)) == {"live"}
    assert run_external(["docker", "container", "inspect", container], check=False).returncode != 0
    assert run_external(["docker", "network", "inspect", network], check=False).returncode != 0
    assert run_external(["docker", "volume", "inspect", volume], check=False).returncode != 0


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_docker
def test_container_cleanup_removes_anonymous_volumes_and_preserves_named_volumes(
    policy_image: str, cleanup_stack: CleanupStack, unique_name: str
) -> None:
    """Disposable databases must not leave anonymous storage after container removal."""
    container = f"sftpwarden-volume-cleanup-{unique_name}"
    named = f"sftpwarden-volume-keep-{unique_name}"
    anonymous: list[str] = []

    def remove_owned_anonymous() -> None:
        for volume in anonymous:
            if run_external(["docker", "volume", "inspect", volume], check=False).returncode == 0:
                run_external(["docker", "volume", "rm", volume])

    cleanup_stack.add(remove_owned_anonymous)
    cleanup_stack.add(lambda: run_external(["docker", "volume", "rm", named]))
    cleanup_stack.add(lambda: remove_docker_container(container))
    run_external(["docker", "volume", "create", named])
    run_external(
        [
            "docker",
            "create",
            "--name",
            container,
            "--volume",
            "/anonymous",
            "--volume",
            f"{named}:/named",
            policy_image,
        ]
    )
    mounts = json.loads(run_external(["docker", "inspect", container]).stdout)[0]["Mounts"]
    anonymous.extend(mount["Name"] for mount in mounts if mount["Destination"] == "/anonymous")
    assert len(anonymous) == 1
    remove_docker_container(container)
    assert run_external(["docker", "volume", "inspect", anonymous[0]], check=False).returncode != 0
    assert run_external(["docker", "volume", "inspect", named], check=False).returncode == 0


@pytest.mark.release_validation
@pytest.mark.release_external
@pytest.mark.release_docker
def test_kubernetes_helper_pid_one_stops_without_forced_termination(
    policy_image: str, cleanup_stack: CleanupStack, unique_name: str
) -> None:
    """The same helper command must handle SIGTERM when it is really container PID 1."""
    container = f"sftpwarden-helper-stop-{unique_name}"
    cleanup_stack.add(lambda: remove_docker_container(container))
    command = idle_helper_command()
    run_external(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--entrypoint",
            command[0],
            policy_image,
            *command[1:],
        ]
    )
    inspect_handler = (
        "import pathlib, signal; "
        "lines = pathlib.Path('/proc/1/status').read_text().splitlines(); "
        "value = next(line.split()[1] for line in lines "
        "if line.startswith('SigCgt:')); "
        "print(bool(int(value, 16) & (1 << (signal.SIGTERM - 1))))"
    )
    eventually(
        lambda: (
            run_external(
                ["docker", "exec", container, "python", "-c", inspect_handler], check=False
            ).stdout.strip()
            == "True"
        ),
        timeout_seconds=10,
        description="helper PID 1 termination handler",
    )
    run_external(["docker", "stop", "--timeout", "2", container], timeout=10)
    assert (
        run_external(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", container]
        ).stdout.strip()
        == "0"
    )
