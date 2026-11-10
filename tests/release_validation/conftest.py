from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"
PUBLIC_KEY_1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFMsDFmvcbuEKKQ2nRx6M4jxyJuS9KoOesN7GyrXw+d+"
PUBLIC_KEY_2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGv5cJFgm0ffn6oHXkIseBv94l3DX7v+2vbF0+qWC+M6"
PUBLIC_KEY_3 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO4yPXotP3j1CbR8K0q0gMB5l+dDIw21jyWrsXIwLFO2"


@dataclass(frozen=True)
class CliResult:
    """Captured SFTPWarden process result."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Path

    @property
    def output(self) -> str:
        """Return combined stdout/stderr for assertions."""
        return self.stdout + self.stderr

    def json(self) -> object:
        """Parse stdout as JSON."""
        return json.loads(self.stdout)


class ReleaseCli:
    """Real CLI runner with isolated home and project working directory."""

    def __init__(self, *, home: Path, cwd: Path) -> None:
        self.home = home
        self.cwd = cwd
        self.env = os.environ.copy()
        self.env.update(
            {
                "SFTPWARDEN_HOME": str(home),
                "PYTHONPATH": str(REPO_ROOT),
                "PYTHONIOENCODING": "utf-8",
                "NO_COLOR": "1",
                "PY_COLORS": "0",
                "TERM": "dumb",
            }
        )
        if os.environ.get("SFTPWARDEN_TEST_INSTALLED_ARTIFACT") == "1":
            self.env.pop("PYTHONPATH", None)

    def run(
        self,
        *args: str | Path,
        input_text: str | None = None,
        timeout: int = 60,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        """Run `python -m sftpwarden` as a user would."""
        isolated = ["-I"] if self.env.get("SFTPWARDEN_TEST_INSTALLED_ARTIFACT") == "1" else []
        command = [sys.executable, *isolated, "-m", "sftpwarden", *[str(arg) for arg in args]]
        env = self.env.copy()
        if extra_env:
            env.update(extra_env)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or self.cwd,
                env=env,
                input=input_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CliResult(
                args=[str(arg) for arg in args],
                returncode=124,
                stdout=stdout,
                stderr=stderr + f"\nCommand timed out after {timeout} seconds.\n",
                cwd=cwd or self.cwd,
            )
        return CliResult(
            args=[str(arg) for arg in args],
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            cwd=cwd or self.cwd,
        )


class CleanupStack:
    """LIFO cleanup registry for resources created by release tests."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._callbacks: list[Callable[[], None]] = []

    def remove_tree(self, path: Path) -> None:
        """Remove only owned test trees, including private container-owned files."""
        path = Path(os.path.abspath(path))
        if path != self.root and self.root not in path.parents:
            raise ValueError("Cleanup target is outside this test's temporary directory")
        # Never let an intermediate symlink redirect privileged cleanup outside
        # the registered tree. rm itself does not follow links within that tree.
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError("Cleanup target contains a symbolic link")
            if parent == self.root:
                break
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except PermissionError:
            if sys.platform != "linux":
                raise
            run_external(["sudo", "-n", "rm", "-rf", "--", str(path)], timeout=60)

    def add(self, callback: Callable[[], None]) -> None:
        """Register one cleanup callback."""
        self._callbacks.append(callback)

    def close(self) -> None:
        """Run cleanup callbacks, keeping the first failure visible."""
        failures: list[BaseException] = []
        while self._callbacks:
            callback = self._callbacks.pop()
            try:
                callback()
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)
        if failures:
            raise AssertionError(f"Release validation cleanup failed: {failures[0]}") from failures[
                0
            ]


@pytest.fixture
def cleanup_stack(tmp_path: Path) -> Iterator[CleanupStack]:
    """Return a cleanup stack that always runs at test end."""
    stack = CleanupStack(tmp_path)
    # Registered first, so private test files disappear after owned processes and
    # infrastructure have been stopped, including when fixture setup fails.
    stack.add(lambda: stack.remove_tree(tmp_path))
    try:
        yield stack
    finally:
        stack.close()


@pytest.fixture
def cli(tmp_path: Path) -> ReleaseCli:
    """Return an isolated real SFTPWarden CLI session."""
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    return ReleaseCli(home=home, cwd=work)


@pytest.fixture
def unique_name() -> str:
    """Return a short unique release-validation resource name."""
    return f"rv-{uuid4().hex[:10]}"


def assert_ok(result: CliResult) -> None:
    """Assert a CLI command succeeded and did not leak internals."""
    assert result.returncode == 0, _format_cli_failure(result)
    assert_no_traceback(result)


def assert_failed(result: CliResult, *expected_fragments: str) -> None:
    """Assert a CLI command failed with a controlled user-facing message."""
    assert result.returncode in {1, 2}, _format_cli_failure(result)
    assert_no_traceback(result)
    semantic_output = " ".join(result.output.split())
    for fragment in expected_fragments:
        assert " ".join(fragment.split()) in semantic_output, _format_cli_failure(result)


def assert_no_traceback(result: CliResult) -> None:
    """Assert errors are controlled instead of raw Python tracebacks."""
    forbidden = ("Traceback (most recent call last)", "During handling of the above exception")
    assert not any(text in result.output for text in forbidden), _format_cli_failure(result)


def _format_cli_failure(result: CliResult) -> str:
    return (
        f"Command: sftpwarden {' '.join(result.args)}\n"
        f"CWD: {result.cwd}\n"
        f"Exit: {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def require_executable(name: str) -> None:
    """Fail clearly when a release test prerequisite is missing."""
    if shutil.which(name) is None:
        pytest.fail(
            f"Release validation requires `{name}` on PATH. "
            "Install the tool or run a narrower non-external subset explicitly."
        )


def run_external(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an external command used by real release scenarios."""
    builder = (env or os.environ).get("SFTPWARDEN_RELEASE_BUILDER")
    if builder and args[:2] == ["docker", "build"]:
        args = ["docker", "buildx", "build", "--builder", builder, "--load", *args[2:]]
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        pytest.fail(
            f"External command failed: {' '.join(args)}\n"
            f"Exit: {completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def eventually(
    action: Callable[[], bool],
    *,
    timeout_seconds: int,
    interval_seconds: float = 1.0,
    description: str,
) -> None:
    """Wait for a real external condition with a clear failure."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if action():
            return
        time.sleep(interval_seconds)
    pytest.fail(f"Timed out waiting for {description}.")


def free_tcp_port() -> int:
    """Return a currently available localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def docker_image_id(image: str) -> str | None:
    """Return a Docker image ID when the tag exists."""
    result = run_external(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def preserve_or_remove_image(
    image: str, previous_id: str | None, *, created_image: bool = False
) -> None:
    """Restore a pre-existing Docker tag or remove a tag created by a test."""
    current_id = docker_image_id(image)
    if not current_id:
        return
    if previous_id and previous_id != current_id:
        run_external(["docker", "image", "tag", previous_id, image], timeout=60)
        if created_image:
            metadata = json.loads(run_external(["docker", "image", "inspect", current_id]).stdout)[
                0
            ]
            if not metadata.get("RepoTags") and not metadata.get("RepoDigests"):
                run_external(["docker", "image", "rm", current_id], timeout=60)
        return
    if previous_id is None:
        run_external(["docker", "image", "rm", image], check=False, timeout=60)


def docker_compose_down(project_root: Path) -> None:
    """Remove Compose resources created for a project."""
    compose_file = project_root / "docker-compose.yml"
    if compose_file.exists():
        run_external(
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.yml",
                "down",
                "-v",
                "--remove-orphans",
            ],
            cwd=project_root,
            check=False,
            timeout=120,
        )


def remove_docker_container(name: str) -> None:
    """Remove an owned container and its anonymous volumes, preserving named volumes."""
    result = run_external(["docker", "rm", "-f", "-v", name], check=False, timeout=60)
    if result.returncode:
        absent = run_external(["docker", "container", "inspect", name], check=False, timeout=30)
        if absent.returncode == 0 or "No such container" not in absent.stderr:
            pytest.fail(f"Cleanup could not confirm removal of the owned container {name}.")


def cleanup_kubernetes_namespace(namespace: str) -> None:
    """Delete a namespace created by release validation."""
    run_external(
        [
            "kubectl",
            "delete",
            "namespace",
            namespace,
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=150s",
        ],
        timeout=180,
    )


def cleanup_helm_release(release: str, namespace: str) -> None:
    """Uninstall a Helm release created by release validation."""
    run_external(
        [
            "helm",
            "uninstall",
            release,
            "--namespace",
            namespace,
            "--ignore-not-found",
            "--wait",
            "--timeout",
            "90s",
        ],
        timeout=120,
    )
