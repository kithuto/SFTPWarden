"""Coordinate cooperating SSH operations without requiring a host Python installation."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import uuid4

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.remote.ssh import remote_path_expression, ssh_base_command
from sftpwarden.system.commands import CommandResult, run
from sftpwarden.utils.errors import RuntimeError


@contextmanager
def operation_lock(
    remote: RemoteEndpoint,
    *,
    create_root: bool = False,
    runner: Callable[[list[str]], CommandResult] | None = None,
) -> Iterator[None]:
    """Keep ownership across upload and activation, even across separate SSH sessions.

    An interrupted client leaves its receipt for explicit recovery. A later client
    cannot steal that receipt; uncertain acquisition is cleaned only by its nonce.
    """
    nonce = uuid4().hex
    common = "\n".join(
        [
            "set -eu",
            "umask 077",
            f"root={remote_path_expression(remote.remote_root)}",
            'parent="$root"',
            'while test "$parent" != / && test "$parent" != .; do',
            '  test ! -L "$parent"; parent=$(dirname -- "$parent")',
            "done",
            *(['mkdir -p -- "$root"'] if create_root else []),
            'cd -- "$root"',
        ]
    )

    def request(script: str) -> None:
        command = [*ssh_base_command(remote), script]
        try:
            result = runner(command) if runner else run(command, timeout=60)
        except Exception:
            raise RuntimeError(
                "Remote operation ownership could not be confirmed; inspect the "
                "project lock before retrying."
            ) from None
        if result.returncode:
            raise RuntimeError(
                "Remote operation lock is held or could not be verified; inspect "
                ".sftpwarden-operation.lock before retrying."
            )

    try:
        request(
            common
            + "\nmkdir .sftpwarden-operation.lock\n"
            + f"printf '%s' {shlex.quote(nonce)} > .sftpwarden-operation.lock/owner"
        )
        yield
    finally:
        request(
            common
            + "\n"
            + "\n".join(
                [
                    "test ! -L .sftpwarden-operation.lock",
                    "if test -d .sftpwarden-operation.lock; then",
                    "  test ! -L .sftpwarden-operation.lock/owner",
                    "  if test -f .sftpwarden-operation.lock/owner && "
                    f'test "$(cat .sftpwarden-operation.lock/owner)" = {nonce}; then',
                    "    rm .sftpwarden-operation.lock/owner",
                    "    rmdir .sftpwarden-operation.lock",
                    "  fi",
                    "fi",
                ]
            )
        )


def locked_project_script(root: str, body: str) -> str:
    """Hold one project lock; a lost owner requires inspection, never automatic stealing.

    The inner shell owns its own temporary-file and maintenance cleanup traps.
    A lock held by a crashed machine is deliberately retained for operator recovery.
    """
    return "\n".join(
        [
            "set -eu",
            "umask 077",
            f"root={remote_path_expression(root)}",
            'parent="$root"',
            'while test "$parent" != / && test "$parent" != .; do',
            '  test ! -L "$parent"; parent=$(dirname -- "$parent")',
            "done",
            'cd -- "$root"',
            "mkdir .sftpwarden-operation.lock",
            "trap 'rmdir .sftpwarden-operation.lock' EXIT",
            "trap 'exit 1' HUP INT TERM",
            "(",
            body,
            ")",
        ]
    )
