from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .conftest import (
    CleanupStack,
    ReleaseCli,
    assert_ok,
    require_executable,
    run_external,
)

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

    run_external(["docker", "build", "-t", image, "."], cwd=dockerfile_root, timeout=120)
    cleanup_stack.add(lambda: run_external(["docker", "image", "rm", "-f", image], check=False))
    run_external(["docker", "create", "--name", container, "--label", label, image], timeout=60)
    cleanup_stack.add(lambda: run_external(["docker", "rm", "-f", container], check=False))
    run_external(["docker", "network", "create", "--label", label, network], timeout=60)
    cleanup_stack.add(lambda: run_external(["docker", "network", "rm", network], check=False))
    run_external(["docker", "volume", "create", "--label", label, volume], timeout=60)
    cleanup_stack.add(lambda: run_external(["docker", "volume", "rm", "-f", volume], check=False))

    shutil.rmtree(stale_root)

    listed = cli.run("context", "ls", "--json", timeout=60)

    assert_ok(listed)
    assert set(_contexts(listed)) == {"live"}
    assert run_external(["docker", "container", "inspect", container], check=False).returncode != 0
    assert run_external(["docker", "network", "inspect", network], check=False).returncode != 0
    assert run_external(["docker", "volume", "inspect", volume], check=False).returncode != 0
