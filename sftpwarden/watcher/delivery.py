"""Validated provider snapshots and bounded, atomic delivery over native SSH."""

from __future__ import annotations

import hashlib
import os
import shlex
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sftpwarden.config import (
    WATCHER_SYNC_PROVIDER_TYPES,
    ProviderType,
    SFTPWardenConfig,
    provider_local_path,
    runtime_config_fingerprints,
)
from sftpwarden.contexts import ContextEntry
from sftpwarden.providers import provider_for_config
from sftpwarden.providers.documents import parse_provider_document
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.remote.locking import locked_project_script
from sftpwarden.remote.ssh import remote_path_expression, ssh_base_command
from sftpwarden.system.commands import command_text
from sftpwarden.users.service import users_fingerprint
from sftpwarden.utils.archives import MAX_METADATA_BYTES, check_managed_path
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH, CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.sqlite import snapshot_sqlite


@dataclass(frozen=True)
class ProviderSnapshot:
    """Exact validated bytes and the independent runtime-policy identity."""

    content: bytes
    digest: str
    policy: str


def read_snapshot(context: ContextEntry, config: SFTPWardenConfig) -> ProviderSnapshot:
    """Reject missing/unstable input and never infer empty policy from a read failure."""
    if config.provider.type not in WATCHER_SYNC_PROVIDER_TYPES:
        raise ContextError("Watcher delivery requires a file provider.")
    root = Path(context.root)
    path = provider_local_path(root, config)
    check_managed_path(root, path.relative_to(root).as_posix())
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContextError("Watcher input must be a regular private provider file.")
    if config.provider.type == ProviderType.SQLITE:
        total = path.stat().st_size
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            check_managed_path(root, sidecar.relative_to(root).as_posix())
            if sidecar.exists():
                total += sidecar.stat().st_size
        if total > MAX_METADATA_BYTES:
            raise ContextError("SQLite provider exceeds the watcher delivery limit.")
        with tempfile.TemporaryDirectory(prefix="sftpwarden-watch-snapshot-") as temporary:
            copied = Path(temporary) / "provider.sqlite"
            snapshot_sqlite(path, copied)
            users = require_configured_schema(provider_for_config(config.provider, copied))
            content = copied.read_bytes()
        return ProviderSnapshot(
            content,
            hashlib.sha256(content).hexdigest(),
            users_fingerprint(users),
        )
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ContextError("Watcher input must be a regular private provider file.")
        content = stream.read(MAX_METADATA_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()

    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    if (
        len(content) > MAX_METADATA_BYTES
        or len(content) != before.st_size
        or identity(before) != identity(after)
        or identity(after) != identity(current)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise ContextError("Provider changed while being read or exceeds the delivery limit.")
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise ContextError("Provider documents must use UTF-8 encoding.") from None
    users = parse_provider_document(
        text, "csv" if config.provider.type == ProviderType.CSV else "yaml"
    )
    if users.schema_version != config.provider.user_schema:
        raise ContextError("Provider schema changed; deploy its matching configuration first.")
    return ProviderSnapshot(content, hashlib.sha256(content).hexdigest(), users_fingerprint(users))


def delivery_script(
    context: ContextEntry,
    config: SFTPWardenConfig,
    remote_path: str,
    snapshot: ProviderSnapshot,
) -> str:
    """Validate transport bytes and deployed provider binding before a same-directory rename."""
    if context.remote is None:
        raise ContextError(f"Context {context.name} is missing remote settings.")
    provider = provider_local_path(context.root, config)
    relative = provider.relative_to(Path(context.root)).as_posix()
    expected = f"{context.remote.remote_root.rstrip('/')}/{relative}"
    if remote_path != expected:
        raise ContextError("Watcher destination differs from the configured provider path.")
    if PurePosixPath(config.provider.path).parent != PurePosixPath(CONTAINER_PROVIDER_DATA_DIR):
        raise ContextError("Deploy the dedicated provider directory before syncing user edits.")
    if PurePosixPath(relative).parent != PurePosixPath("provider"):
        raise ContextError("Watcher delivery must stay inside the dedicated provider directory.")
    provider_digest, config_digest = runtime_config_fingerprints(config)
    verify = (
        "from sftpwarden.runtime.configuration import check_desired_provider; "
        f"check_desired_provider({CONTAINER_CONFIG_PATH!r}, "
        f"desired_provider_fingerprint={provider_digest!r}, "
        f"desired_config_fingerprint={config_digest!r})"
    )
    check = shlex.join(
        [
            "docker",
            "compose",
            "-f",
            context.remote.compose_file,
            "exec",
            "-T",
            "sftpwarden",
            "python",
            "-c",
            verify,
        ]
    )
    visible = shlex.join(
        [
            "docker",
            "compose",
            "-f",
            context.remote.compose_file,
            "exec",
            "-T",
            "sftpwarden",
            "python",
            "-c",
            (
                "import hashlib,os; from pathlib import Path; "
                "from sftpwarden.runtime.configuration import load_runtime_config; "
                f"c=load_runtime_config({CONTAINER_CONFIG_PATH!r}); "
                "p=Path(c.provider.path); f=p.open('rb'); "
                f"assert hashlib.file_digest(f,'sha256').hexdigest()=={snapshot.digest!r}; "
                "os.fsync(f.fileno()); f.close(); "
                "d=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)"
            ),
        ]
    )
    sqlite_checks = (
        [
            f'test ! -e "$target{suffix}" && test ! -L "$target{suffix}"'
            for suffix in ("-wal", "-shm", "-journal")
        ]
        if config.provider.type == ProviderType.SQLITE
        else []
    )
    # The operator owns the project and its provider directory. Neither can be
    # traversed through a link. Only a unique temporary in that directory is removed.
    body = "\n".join(
        [
            "set -eu",
            "umask 077",
            f"root={remote_path_expression(context.remote.remote_root)}",
            'parent="$root"',
            'while test "$parent" != / && test "$parent" != .; do',
            '  test ! -L "$parent"; parent=$(dirname -- "$parent")',
            "done",
            'cd -- "$root"',
            "test -d provider",
            "test ! -L provider",
            f"target={shlex.quote(relative)}",
            'test ! -L "$target"',
            'test ! -e "$target" || test -f "$target"',
            *sqlite_checks,
            "temporary=$(mktemp provider/.sftpwarden-delivery.XXXXXXXX)",
            "trap 'rm -f -- \"$temporary\"' EXIT",
            "trap 'exit 1' HUP INT TERM",
            'cat > "$temporary"',
            f'test "$(wc -c < "$temporary")" -eq {len(snapshot.content)}',
            "if command -v sha256sum >/dev/null 2>&1; then",
            '  actual=$(sha256sum < "$temporary"); actual=${actual%% *}',
            "elif command -v shasum >/dev/null 2>&1; then",
            '  actual=$(shasum -a 256 < "$temporary"); actual=${actual%% *}',
            "else exit 1; fi",
            f'test "$actual" = {shlex.quote(snapshot.digest)}',
            f"{check} >/dev/null 2>&1",
            'test ! -L "$target"',
            "test ! -L provider",
            *sqlite_checks,
            'chmod 600 "$temporary"',
            'mv -f -- "$temporary" "$target"',
            f"{visible} >/dev/null 2>&1",
        ]
    )
    return locked_project_script(context.remote.remote_root, body)


def deliver_snapshot(
    context: ContextEntry,
    config: SFTPWardenConfig,
    remote_path: str,
    snapshot: ProviderSnapshot,
    *,
    dry_run: bool = False,
) -> str:
    """Use the same SSH transport on Linux, macOS and Windows without shelling local input."""
    script = delivery_script(context, config, remote_path, snapshot)
    assert context.remote is not None
    command = [*ssh_base_command(context.remote), script]
    if dry_run:
        return command_text(command)
    try:
        result = subprocess.run(
            command,
            input=snapshot.content,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ContextError("Provider delivery failed or exceeded its 60-second deadline.") from None
    if result.returncode:
        raise ContextError(
            "Provider delivery failed; its version has not been acknowledged.",
            suggestion="Check SSH trust/access and deploy the current provider configuration.",
        )
    return "Delivered validated provider bytes."
