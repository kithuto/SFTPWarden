"""Explicit private environment delivery for remote local-sync deployments."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

from sftpwarden.contexts import ContextEntry
from sftpwarden.remote.ssh import remote_path_expression, ssh_base_command
from sftpwarden.utils.errors import ContextError


def deliver_environment(context: ContextEntry) -> None:
    """Publish an explicitly selected .env over stdin, with no credential-bearing argv."""
    if not context.remote or not context.root or context.storage != "local-sync":
        raise ContextError("Environment delivery requires a remote local-sync project.")
    path = Path(context.root) / ".env"
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            content = source.read(1024 * 1024 + 1)
            closed = os.fstat(source.fileno())
        after = path.lstat()
        versions = {
            (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
            for item in (before, opened, closed, after)
        }
        if len(versions) != 1 or len(content) != before.st_size or len(content) > 1024 * 1024:
            raise ValueError
        content.decode("utf-8")
    except (OSError, ValueError):
        raise ContextError(
            "include_env requires a stable, readable UTF-8 .env regular file under "
            "the local project."
        ) from None
    digest = hashlib.sha256(content).hexdigest()
    script = "\n".join(
        [
            "set -eu",
            "umask 077",
            f"root={remote_path_expression(context.remote.remote_root)}",
            'parent="$root"',
            'while test "$parent" != / && test "$parent" != .; do',
            '  test ! -L "$parent"; parent=$(dirname -- "$parent")',
            "done",
            'mkdir -p -- "$root"',
            'cd -- "$root"',
            "test ! -L .env",
            "test ! -e .env || test -f .env",
            "temporary=$(mktemp .sftpwarden-env.XXXXXXXX)",
            "trap 'rm -f -- \"$temporary\"' EXIT",
            "trap 'exit 1' HUP INT TERM",
            'cat > "$temporary"',
            f'test "$(wc -c < "$temporary")" -eq {len(content)}',
            "if command -v sha256sum >/dev/null 2>&1; then",
            '  actual=$(sha256sum < "$temporary"); actual=${actual%% *}',
            "elif command -v shasum >/dev/null 2>&1; then",
            '  actual=$(shasum -a 256 < "$temporary"); actual=${actual%% *}',
            "else exit 1; fi",
            f'test "$actual" = {digest}',
            'chmod 600 "$temporary"',
            "test ! -L .env",
            'mv -f -- "$temporary" .env',
        ]
    )
    try:
        result = subprocess.run(
            [*ssh_base_command(context.remote), script],
            input=content,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise ContextError(
            "Private environment delivery failed or exceeded its deadline."
        ) from None
    if result.returncode:
        raise ContextError("Private environment delivery failed; deployment was not started.")
