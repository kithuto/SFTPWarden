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
FAKE_KEY_1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForReleaseValidation"
FAKE_KEY_2 = "ssh-ed25519 ZmFrZS1yZWxlYXNlLWtleS0y"
FAKE_KEY_3 = "ssh-ed25519 ZmFrZS1yZWxlYXNlLWtleS0z"


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

    def run(
        self,
        *args: str | Path,
        input_text: str | None = None,
        timeout: int = 60,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        """Run `python -m sftpwarden` as a user would."""
        command = [sys.executable, "-m", "sftpwarden", *[str(arg) for arg in args]]
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

    def __init__(self) -> None:
        self._callbacks: list[Callable[[], None]] = []

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
def cleanup_stack() -> Iterator[CleanupStack]:
    """Return a cleanup stack that always runs at test end."""
    stack = CleanupStack()
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
    assert result.returncode != 0, f"Command unexpectedly succeeded: {result.args}\n{result.output}"
    assert_no_traceback(result)
    for fragment in expected_fragments:
        assert fragment in result.output, _format_cli_failure(result)


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


def preserve_or_remove_image(image: str, previous_id: str | None) -> None:
    """Restore a pre-existing Docker tag or remove a tag created by a test."""
    current_id = docker_image_id(image)
    if not current_id:
        return
    if previous_id and previous_id != current_id:
        run_external(["docker", "image", "tag", previous_id, image], check=False, timeout=60)
        run_external(["docker", "image", "rm", current_id], check=False, timeout=60)
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
    """Force-remove one Docker container if it exists."""
    run_external(["docker", "rm", "-f", name], check=False, timeout=60)


def cleanup_kubernetes_namespace(namespace: str) -> None:
    """Delete a namespace created by release validation."""
    run_external(
        ["kubectl", "delete", "namespace", namespace, "--ignore-not-found=true", "--wait=false"],
        check=False,
        timeout=120,
    )


def cleanup_helm_release(release: str, namespace: str) -> None:
    """Uninstall a Helm release created by release validation."""
    run_external(
        ["helm", "uninstall", release, "--namespace", namespace],
        check=False,
        timeout=120,
    )
