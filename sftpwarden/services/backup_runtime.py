"""Explicit bounded downtime for authoritative local Compose recovery capture."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO
from uuid import uuid4

from sftpwarden.config import FILE_PROVIDER_TYPES, DeployTarget, SFTPWardenConfig, load_config
from sftpwarden.contexts import ContextEntry, ContextType
from sftpwarden.remote.deploy import remote_preparation_image
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_binary_output, atomic_bytes
from sftpwarden.utils.state_transactions import state_transaction

if TYPE_CHECKING:
    from sftpwarden.services.backup import BackupResult


def _run(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Runtime backup command failed or exceeded its deadline.") from None
    if result.returncode:
        raise RuntimeError("Runtime backup command failed; inspect the selected Docker target.")
    return result


def _mount(source: Path, target: str) -> str:
    stream = io.StringIO()
    csv.writer(stream, lineterminator="").writerow(
        [
            "type=bind",
            f"source={source.resolve()}",
            f"target={target}",
            "readonly",
        ]
    )
    return stream.getvalue()


def _resume(container: str) -> None:
    _run(["docker", "start", container])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = json.loads(
            _run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    container,
                ]
            ).stdout
        )
        if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
            return
        if not state.get("Running"):
            break
        time.sleep(1)
    raise RuntimeError("The runtime did not become healthy after backup maintenance.")


def _entries(config: SFTPWardenConfig, *, include_data: bool) -> list[str]:
    paths = [
        "sftpwarden.yaml",
        config.docker.compose_file,
        "host_keys",
        "state",
    ]
    if config.provider.type in FILE_PROVIDER_TYPES:
        paths.append(f"provider/{PurePosixPath(config.provider.path).name}")
    if include_data:
        paths.append("data")
    return paths


@dataclass
class RuntimeCapture:
    """Selected runtime identity and the availability state to restore on exit."""

    container: str
    applied: Path
    config: SFTPWardenConfig
    info: dict[str, Any]
    resume_on_exit: bool = True


@contextmanager
def inspect_runtime(entry: ContextEntry) -> Iterator[RuntimeCapture]:
    """Read the selected applied config without stopping or changing the runtime."""
    config = load_config(entry.config)
    if entry.type != ContextType.LOCAL or config.deploy.target != DeployTarget.COMPOSE:
        raise RuntimeError("This maintenance route requires a local Compose runtime.")
    with tempfile.TemporaryDirectory(prefix="sftpwarden-runtime-backup-") as temporary:
        info = json.loads(_run(["docker", "inspect", config.docker.container_name]).stdout)[0]
        container = info["Id"]
        labels = info.get("Config", {}).get("Labels", {}) or {}
        working = labels.get("com.docker.compose.project.working_dir")
        if not working or Path(working).resolve() != Path(entry.root).resolve():
            raise RuntimeError("The selected container does not belong to this Compose project.")
        if info["State"].get("Paused") or info["State"].get("Restarting"):
            raise RuntimeError(
                "The selected runtime is paused or restarting; resolve its state first."
            )
        applied = Path(temporary) / "applied.json"
        _run(["docker", "cp", f"{container}:/run/sftpwarden/applied-config.json", str(applied)])
        applied.chmod(0o600)
        record = json.loads(applied.read_text(encoding="utf-8"))
        if record.get("format") != 1:
            raise RuntimeError("Unsupported applied runtime configuration.")
        yield RuntimeCapture(
            container,
            applied,
            SFTPWardenConfig.model_validate(record["config"]),
            info,
        )


@contextmanager
def stopped_runtime(runtime: RuntimeCapture) -> Iterator[None]:
    """Keep a single maintenance window across capture and optional restore effects."""
    was_running = bool(runtime.info["State"].get("Running"))
    try:
        if was_running:
            _run(["docker", "stop", "--time", "30", runtime.container], timeout=45)
        yield
    finally:
        if was_running and runtime.resume_on_exit:
            _resume(runtime.container)


def capture_runtime(runtime: RuntimeCapture, stream: BinaryIO, *, include_data: bool) -> None:
    """Stream a stopped runtime through a temporary Linux helper with bounded cleanup."""
    helper = f"sftpwarden-backup-{uuid4().hex}"
    network = "none"
    if runtime.config.provider.type not in FILE_PROVIDER_TYPES:
        network = runtime.info.get("HostConfig", {}).get("NetworkMode", "")
        if not network or network == "none" or network.startswith("container:"):
            raise RuntimeError("External provider capture requires an inspectable runtime network.")
    private_info = runtime.applied.with_name("container.json")
    atomic_bytes(private_info, json.dumps(runtime.info).encode())
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        helper,
        "--label",
        f"sftpwarden.capture={helper}",
        "--network",
        network,
        "--volumes-from",
        f"{runtime.container}:ro",
        "--mount",
        _mount(runtime.applied, "/recovery.json"),
        "--mount",
        _mount(private_info, "/container.json"),
        "--entrypoint",
        "python",
        remote_preparation_image(),
        "-m",
        "sftpwarden.runtime.backup",
        "--applied",
        "/recovery.json",
        "--container-info",
        "/container.json",
    ]
    if include_data:
        command.append("--include-data")
    try:
        try:
            result = subprocess.run(
                command,
                stdout=stream,
                stderr=subprocess.PIPE,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Runtime backup capture failed or exceeded its deadline.") from None
        if result.returncode:
            raise RuntimeError("Runtime backup capture failed; previous output is unchanged.")
    finally:
        cleanup_capture_helper(helper)


def cleanup_capture_helper(helper: str) -> None:
    """Do not infer absence from a failed daemon lookup or delete another owner's container."""
    try:
        inspected = subprocess.run(
            ["docker", "container", "inspect", helper],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if inspected.returncode:
            remaining = _run(
                ["docker", "container", "ls", "--all", "--quiet", "--filter", f"name=^/{helper}$"]
            )
            if remaining.stdout.strip():
                raise ValueError
            return
        details = json.loads(inspected.stdout)[0]
        if details.get("Config", {}).get("Labels", {}).get("sftpwarden.capture") != helper:
            raise ValueError
        _run(["docker", "rm", "--force", details["Id"]])
    except Exception:
        raise RuntimeError(
            "Could not confirm cleanup of the owned runtime backup helper; inspect it "
            "before retrying."
        ) from None


def create_runtime_backup(
    entry: ContextEntry,
    output: Path,
    *,
    include_data: bool,
    dry_run: bool,
) -> BackupResult:
    """Stop only the selected runtime and publish after restoring its previous availability."""
    from sftpwarden.services.backup import BackupResult

    config = load_config(entry.config)
    if entry.type != ContextType.LOCAL or config.deploy.target != DeployTarget.COMPOSE:
        raise RuntimeError("This maintenance route requires a local Compose runtime.")
    entries = _entries(config, include_data=include_data)
    if dry_run:
        return BackupResult(path=output, entries=entries)
    with state_transaction(Path(entry.config)), inspect_runtime(entry) as runtime:
        entries = _entries(runtime.config, include_data=include_data)
        with atomic_binary_output(output) as stream, stopped_runtime(runtime):
            capture_runtime(runtime, stream, include_data=include_data)
    return BackupResult(path=output, entries=entries)
