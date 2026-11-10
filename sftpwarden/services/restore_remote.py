"""Fresh remote recovery with explicit target inspection and no rsync overwrite."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path, PurePosixPath
from uuid import uuid4

from sftpwarden.contexts import ContextEntry
from sftpwarden.remote.deploy import remote_preparation_image
from sftpwarden.remote.locking import locked_project_script
from sftpwarden.remote.ssh import remote_path_expression, ssh_base_command
from sftpwarden.utils.errors import RuntimeError


def fresh_restore_script(
    entry: ContextEntry, *, include_data: bool, dry_run: bool, protected_external: bool = False
) -> str:
    """Check the root without creating it, and hold a parent lock during publication."""
    if entry.remote is None:
        raise RuntimeError("Remote recovery requires an explicit SSH endpoint.")
    remote = PurePosixPath(entry.remote.remote_root)
    if remote.name in {"", ".", "..", "~"} or ".." in remote.parts:
        raise RuntimeError("Remote recovery requires a dedicated destination directory.")
    if entry.remote.remote_config != str(remote / "sftpwarden.yaml"):
        raise RuntimeError("Fresh remote recovery requires sftpwarden.yaml in the selected root.")
    parent = str(remote.parent)
    check = "\n".join(
        [
            "set -eu",
            f"target={shlex.quote(remote.name)}",
            'test ! -L "$target"',
            'if test -e "$target"; then',
            '  test -d "$target"',
            # find must succeed; inaccessible content must never be interpreted as empty.
            '  contents=$(find "$target" -mindepth 1 -maxdepth 1 -print)',
            '  test -z "$contents"',
            "fi",
        ]
    )
    if dry_run:
        return f"set -eu\ncd -- {remote_path_expression(parent)}\n{check}"
    helper = "sftpwarden-restore-" + uuid4().hex
    image = shlex.quote(remote_preparation_image())
    body = "\n".join(
        [
            check,
            "parent=$(pwd -P)",
            # Docker -v uses a colon delimiter even inside a correctly quoted argument.
            'case "$parent" in *:*) exit 1;; esac',
            "cleanup() {",
            "  status=$?; trap - EXIT HUP INT TERM; set +e",
            "  if owner=$(docker inspect --format "
            f"'{{{{index .Config.Labels \"sftpwarden.recovery\"}}}}' {helper} 2>/dev/null); then",
            f'    if test "$owner" = {helper}; then '
            f"docker rm -f {helper} >/dev/null 2>&1 || status=1; else status=1; fi",
            "  else",
            "    remaining=$(docker container ls --all --quiet "
            f"--filter 'name=^/{helper}$') || status=1",
            '    test -z "$remaining" || status=1',
            "  fi",
            '  exit "$status"',
            "}",
            "trap cleanup EXIT",
            "trap 'exit 1' HUP INT TERM",
            f"docker run --name {helper} --label sftpwarden.recovery={helper} "
            '--network none -i -v "$parent:/destination" '
            f"--entrypoint python {image} -m sftpwarden.runtime.restore "
            '--root "/destination/$target" --uid "$(id -u)" --gid "$(id -g)"'
            + (" --include-data" if include_data else ""),
            # External contents were restored and checked separately; this helper never writes a DB.
        ]
    )
    if protected_external:
        body += " --protected-external"
    return locked_project_script(parent, body)


def restore_fresh_remote(
    entry: ContextEntry,
    archive: Path,
    *,
    include_data: bool,
    dry_run: bool,
    protected_external: bool = False,
) -> None:
    """Stream a prevalidated local archive; a lost reply never proves successful recovery."""
    script = fresh_restore_script(
        entry, include_data=include_data, dry_run=dry_run, protected_external=protected_external
    )
    assert entry.remote is not None
    try:
        with archive.open("rb") as stream:
            result = subprocess.run(
                [*ssh_base_command(entry.remote), script],
                stdin=subprocess.DEVNULL if dry_run else stream,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60 if dry_run else 540,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "Remote recovery did not finish; inspect the destination before retrying."
        ) from None
    if result.returncode:
        raise RuntimeError(
            "Remote recovery requires a verified empty, writable destination.",
            suggestion=(
                "Preserve existing or damaged installations and select a separate empty "
                "remote root."
            ),
        )
