"""Capture the selected remote Compose runtime through private native SSH streams."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from sftpwarden.contexts import ContextEntry
from sftpwarden.remote.deploy import remote_preparation_image
from sftpwarden.remote.locking import locked_project_script
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_binary_output

if TYPE_CHECKING:
    from sftpwarden.services.backup import BackupResult


def capture_script(entry: ContextEntry, *, include_data: bool) -> str:
    """Select by Compose identity, stop, capture actual mounts, and restore prior availability."""
    if entry.remote is None:
        raise RuntimeError("Remote backup requires an explicit SSH endpoint.")
    compose = shlex.join(["docker", "compose", "-f", entry.remote.compose_file])
    helper = f"sftpwarden-backup-{uuid4().hex}"
    image = shlex.quote(remote_preparation_image())
    body = "\n".join(
        [
            "set -eu",
            'temporary=""',
            'container=""',
            "resume=0",
            "cleanup() {",
            "  status=$?; trap - EXIT HUP INT TERM; set +e",
            "  if owner=$(docker inspect --format "
            f"'{{{{index .Config.Labels \"sftpwarden.capture\"}}}}' {helper} 2>/dev/null); then",
            f'    if test "$owner" = {helper}; then '
            f"docker rm -f {helper} >/dev/null 2>&1 || status=1; else status=1; fi",
            "  else",
            "    remaining=$(docker container ls --all --quiet "
            f"--filter 'name=^/{helper}$') || status=1",
            '    test -z "$remaining" || status=1',
            "  fi",
            '  if test "$resume" -eq 1; then',
            '    docker start "$container" >/dev/null 2>&1 || status=1',
            "    healthy=0; attempt=0",
            '    while test "$attempt" -lt 90; do',
            (
                "      observed=$(docker inspect --format '{{.State.Health.Status}}' "
                '"$container" 2>/dev/null)'
            ),
            '      if test "$observed" = healthy; then healthy=1; break; fi',
            "      attempt=$((attempt + 1)); sleep 1",
            "    done",
            '    test "$healthy" -eq 1 || status=1',
            "  fi",
            '  if test -n "$temporary"; then rm -rf -- "$temporary" || status=1; fi',
            '  exit "$status"',
            "}",
            "trap cleanup EXIT",
            "trap 'exit 1' HUP INT TERM",
            f"container=$({compose} ps --all --quiet sftpwarden)",
            'test -n "$container"',
            # A single immutable ID prevents name reuse and accidental multi-replica capture.
            'case "$container" in *[!a-f0-9]*) exit 1;; esac',
            (
                "test \"$(docker inspect --format '{{.State.Paused}} "
                "{{.State.Restarting}}' \"$container\")\" = 'false false'"
            ),
            'temporary=$(mktemp -d "${TMPDIR:-/tmp}/sftpwarden-capture.XXXXXXXX")',
            (
                'docker cp "$container:/run/sftpwarden/applied-config.json" '
                '"$temporary/applied.json" >/dev/null'
            ),
            'test -f "$temporary/applied.json" && test ! -L "$temporary/applied.json"',
            'chmod 600 "$temporary/applied.json"',
            # The Go template selects only this immutable container; the helper accepts
            # its JSON object without requiring Python/jq on the remote host.
            'docker inspect --format \'{{json .}}\' "$container" > "$temporary/container.json"',
            'chmod 600 "$temporary/container.json"',
            "network=$(docker inspect --format '{{.HostConfig.NetworkMode}}' \"$container\")",
            'case "$network" in ""|container:*) exit 1;; esac',
            'if test "$(docker inspect --format \'{{.State.Running}}\' "$container")" = true; then',
            # Arm resumption before stopping, including stop timeouts/partial failures.
            '  resume=1; docker stop --time 30 "$container" >/dev/null',
            "fi",
            # Create separately so cleanup never has to infer whether --rm already removed it.
            f"docker create --name {helper} --label sftpwarden.capture={helper} "
            '--network "$network" --volumes-from "$container:ro" '
            f"--entrypoint python {image} -m sftpwarden.runtime.backup "
            "--applied /recovery.json --container-info /container.json "
            "--topology remote-compose-runtime"
            + (" --include-data" if include_data else "")
            + " >/dev/null",
            f'docker cp "$temporary/applied.json" {helper}:/recovery.json >/dev/null',
            f'docker cp "$temporary/container.json" {helper}:/container.json >/dev/null',
            f"docker start --attach {helper}",
            # docker start --attach does not consistently propagate the container's exit code.
            f"test \"$(docker inspect --format '{{{{.State.ExitCode}}}}' {helper})\" -eq 0",
        ]
    )
    return locked_project_script(entry.remote.remote_root, body)


def create_remote_runtime_backup(
    entry: ContextEntry,
    output: Path,
    *,
    include_data: bool,
    dry_run: bool,
    maintenance: bool,
) -> BackupResult:
    """Publish only after successful remote cleanup and complete local archive verification."""
    from sftpwarden.services.backup import BackupResult
    from sftpwarden.services.restore_existing import verify_safety_archive

    if entry.remote is None:
        raise RuntimeError("Remote backup requires an explicit SSH endpoint.")
    if not maintenance:
        raise RuntimeError(
            "Remote recovery capture requires --maintenance and paused external writers.",
            suggestion="Pause the watcher and provider/data writers before the maintenance backup.",
        )
    if dry_run:
        return BackupResult(
            path=output,
            entries=[
                "remote:applied-config",
                "remote:provider",
                "remote:host_keys",
                "remote:state",
                *(["remote:data"] if include_data else []),
            ],
        )
    script = capture_script(entry, include_data=include_data)
    with tempfile.TemporaryDirectory(prefix="sftpwarden-remote-capture-") as directory:
        staged = Path(directory) / "capture.tar.gz"
        with staged.open("xb") as stream:
            staged.chmod(0o600)
            try:
                result = subprocess.run(
                    [*ssh_base_command(entry.remote), script],
                    stdout=stream,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=540,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError(
                    "Remote capture did not finish; inspect runtime availability and "
                    "its operation lock."
                ) from None
        if result.returncode:
            raise RuntimeError(
                "Remote capture or maintenance cleanup failed; no backup was published.",
                suggestion=(
                    "Inspect the remote runtime, backup helper, and operation lock before retrying."
                ),
            )
        try:
            manifest = verify_safety_archive(staged)
        except (tarfile.TarError, OSError, ValueError):
            raise RuntimeError(
                "Remote capture returned an invalid archive; no backup was published."
            ) from None
        with staged.open("rb") as source, atomic_binary_output(output) as target:
            shutil.copyfileobj(source, target)
    return BackupResult(path=output, entries=manifest.entries)
