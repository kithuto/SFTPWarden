"""Validate and apply staged Linux recovery metadata before OpenSSH can start."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sftpwarden.config import SFTPWardenConfig, load_config
from sftpwarden.runtime.configuration import APPLIED_CONFIG_PATH, load_runtime_config
from sftpwarden.runtime.core import RuntimeState
from sftpwarden.runtime.lifecycle import runtime_lock
from sftpwarden.utils.archives import archive_relative_path
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes, sync_directory

RECOVERY_FILE = "restore-metadata.json"
STATUS_FILE = "restore-status.json"
BLOCK_FILE = "restore-in-progress.json"


class RecoveryMember(BaseModel):
    """Bound privileged metadata and forbid special permissions or ownership sentinels."""

    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["file", "directory"]
    size: int = Field(ge=0, le=100 * 1024**3)
    mode: int = Field(ge=0, le=0o777)
    uid: int = Field(ge=0, le=2**32 - 2)
    gid: int = Field(ge=0, le=2**32 - 2)
    sha256: str


def validated_metadata(
    members: Mapping[str, object],
    state: RuntimeState,
    config: SFTPWardenConfig,
) -> dict[str, RecoveryMember]:
    """Limit data ownership to retained identities and privileged operational roots."""
    result: dict[str, RecoveryMember] = {}
    for name, raw in members.items():
        parts = archive_relative_path(name).parts
        member = RecoveryMember.model_validate(raw)
        if member.type == "file" and (
            len(member.sha256) != 64 or any(c not in "0123456789abcdef" for c in member.sha256)
        ):
            raise RuntimeError("Invalid recovery content fingerprint.")
        if member.type == "directory" and (member.sha256 or member.size):
            raise RuntimeError("Invalid recovery directory metadata.")
        if parts[0] in {"state", "host_keys"}:
            member.uid = member.gid = 0
            member.mode = 0o700 if member.type == "directory" else 0o600
            if parts[0] == "host_keys" and name.endswith(".pub"):
                member.mode = 0o644
        elif parts[0] == "data":
            if len(parts) <= 2:
                member.uid = member.gid = 0
                if member.type == "directory":
                    member.mode = int(config.isolation.root_permissions, 8)
            else:
                validate_data_owner(name, (member.uid, member.gid), state)
        else:
            raise RuntimeError("Recovery metadata includes an unmanaged destination.")
        if name in {f"state/{RECOVERY_FILE}", f"state/{STATUS_FILE}", f"state/{BLOCK_FILE}"}:
            raise RuntimeError(
                "An unfinished recovery cannot be imported as ordinary runtime state."
            )
        result[name] = member
    return result


def validate_data_owner(name: str, owner: tuple[int, int], state: RuntimeState) -> None:
    """Reject data ownership that cannot be safely restored using the retained identities."""
    parts = archive_relative_path(name).parts
    if parts[0] != "data" or len(parts) < 3:
        return
    identity = state.users.get(parts[1])
    if identity is None or owner[0] not in {0, identity.uid} or owner[1] not in {0, identity.gid}:
        raise RuntimeError("Recovery data ownership does not match retained user identities.")


def write_recovery_record(
    root: Path,
    metadata: Mapping[str, object],
    config: SFTPWardenConfig,
    *,
    requires_recreate: bool = False,
) -> None:
    """Retain validated ownership for application in the Linux runtime at activation."""
    if not metadata:
        return
    state_file = root / "state" / "state.json"
    if not state_file.is_file():
        raise RuntimeError("Runtime recovery requires the retained state and UID/GID mappings.")
    state = RuntimeState.load(state_file)
    members = validated_metadata(metadata, state, config)
    roots = {
        "data": config.server.data_dir,
        "state": config.server.state_dir,
        "host_keys": config.server.host_keys_dir,
    }
    atomic_bytes(
        root / "state" / RECOVERY_FILE,
        json.dumps(
            {
                "format": 1,
                "roots": roots,
                "requires_recreate": requires_recreate,
                "members": {name: item.model_dump() for name, item in members.items()},
            },
            sort_keys=True,
        ).encode(),
    )


def _open_member(
    roots: dict[str, int],
    name: str,
    member: RecoveryMember,
    stack: ExitStack,
) -> int:
    parts = archive_relative_path(name).parts
    parent = roots[parts[0]]
    if len(parts) == 1:
        return parent
    for part in parts[1:-1]:
        parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, parent)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if member.type == "directory":
        flags |= os.O_DIRECTORY
    descriptor = os.open(parts[-1], flags, dir_fd=parent)
    stack.callback(os.close, descriptor)
    return descriptor


def _verify_payload(descriptor: int, member: RecoveryMember) -> None:
    info = os.fstat(descriptor)
    if member.type == "directory":
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Recovery directory was replaced before activation.")
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != member.size:
        raise RuntimeError("Recovery file was replaced before activation.")
    with os.fdopen(os.dup(descriptor), "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != member.sha256:
        raise RuntimeError("Recovery content changed before activation.")


def apply_recovery_metadata(config: SFTPWardenConfig) -> None:
    """Keep the retry record until all bytes, ownership and permissions are verified."""
    directory = Path(config.server.state_dir)
    blocker = directory / BLOCK_FILE
    if blocker.exists() or blocker.is_symlink():
        raise RuntimeError("File restoration is incomplete; keep the runtime stopped and recover.")
    pending = directory / RECOVERY_FILE
    if not pending.exists() and not pending.is_symlink():
        return
    with runtime_lock(directory), ExitStack() as roots_stack:
        descriptor = os.open(pending, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            record = json.load(stream)
        locations = {
            "data": config.server.data_dir,
            "state": config.server.state_dir,
            "host_keys": config.server.host_keys_dir,
        }
        if record.get("format") != 1 or record.get("roots") != locations:
            raise RuntimeError("Recovery paths changed before activation.")
        if record.get("requires_recreate") and APPLIED_CONFIG_PATH.exists():
            raise RuntimeError("Restored files require explicit deployment into a new container.")
        state = RuntimeState.load(directory / "state.json")
        members = validated_metadata(record["members"], state, config)
        roots = {}
        for name in {archive_relative_path(name).parts[0] for name in members}:
            root_fd = os.open(locations[name], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            roots_stack.callback(os.close, root_fd)
            roots[name] = root_fd
        # Complete preflight before any privileged metadata effect.
        try:
            for name, member in members.items():
                with ExitStack() as stack:
                    _verify_payload(_open_member(roots, name, member, stack), member)
        except Exception:  # noqa: BLE001
            atomic_bytes(
                directory / STATUS_FILE,
                json.dumps(
                    {
                        "format": 1,
                        "completed_members": 0,
                        "pending_members": len(members),
                        "phase": "validation_failed",
                    }
                ).encode(),
            )
            raise
        completed = 0
        try:
            for name, member in members.items():
                with ExitStack() as stack:
                    descriptor = _open_member(roots, name, member, stack)
                    _verify_payload(descriptor, member)
                    os.fchown(descriptor, member.uid, member.gid)
                    os.fchmod(descriptor, member.mode)
                    actual = os.fstat(descriptor)
                    if (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (
                        member.uid,
                        member.gid,
                        member.mode,
                    ):
                        raise RuntimeError(
                            "The runtime filesystem did not retain recovery metadata."
                        )
                    os.fsync(descriptor)
                    completed += 1
        finally:
            atomic_bytes(
                directory / STATUS_FILE,
                json.dumps(
                    {
                        "format": 1,
                        "completed_members": completed,
                        "pending_members": len(members) - completed,
                        "phase": "metadata_applied"
                        if completed == len(members)
                        else "retry_required",
                    }
                ).encode(),
            )
        pending.unlink()
        sync_directory(directory)


def main() -> int:
    """Apply recovery before keys, daemon validation or reconciliation can change state."""
    try:
        path = os.environ["SFTPWARDEN_CONFIG"]
        config = load_runtime_config(path) if APPLIED_CONFIG_PATH.exists() else load_config(path)
        apply_recovery_metadata(config)
        return 0
    except Exception:  # noqa: BLE001
        print(
            "Recovery verification failed; inspect private restore status before retrying.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
