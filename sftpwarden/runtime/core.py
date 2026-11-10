from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from sftpwarden.config import SFTPWardenConfig
from sftpwarden.providers import ProviderUsers, SFTPUser, load_users, users_fingerprint
from sftpwarden.runtime.configuration import load_runtime_config as load_config
from sftpwarden.runtime.execution import execute_pass
from sftpwarden.runtime.filesystem import ensure_upload_tree
from sftpwarden.runtime.lifecycle import RETRY_CAP_SECONDS, atomic_json, read_json, runtime_lock
from sftpwarden.runtime.polling import provider_stamp, wait_for_provider_change
from sftpwarden.system.commands import run_checked
from sftpwarden.users.models import SFTPUserKey
from sftpwarden.users.service import runtime_user_data
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import sync_directory

pwd: ModuleType | None
grp: ModuleType | None
try:
    import grp as _grp
except ModuleNotFoundError:
    grp = None
else:
    grp = _grp

try:
    import pwd as _pwd
except ModuleNotFoundError:
    pwd = None
else:
    pwd = _pwd

SSHD_CONFIG_PATH = Path("/etc/ssh/sshd_config")
DISABLED_PASSWORD_HASH = "!"
NO_PASSWORD_HASH = "*"


@dataclass
class RuntimeUserState:
    """Persisted runtime identity state for one user."""

    uid: int
    gid: int
    disabled: bool = False
    applied_fingerprint: str | None = None


@dataclass
class RuntimeState:
    """Persisted runtime state for all managed users."""

    users: dict[str, RuntimeUserState]
    fingerprint: str | None = None
    pending: bool = False

    @classmethod
    def load(cls, path: Path) -> RuntimeState:
        """Load runtime state from disk.

        Parameters
        ----------
        path
            State file path.

        Returns
        -------
        RuntimeState
            Loaded state, or an empty state when missing.
        """
        if not path.exists():
            return cls(users={})
        data = json.loads(path.read_text(encoding="utf-8"))
        users = parse_state_users(data)
        for username, identity in users.items():
            SFTPUser(username=username, uid=identity.uid, gid=identity.gid)
        for attribute in ("uid", "gid"):
            values = [getattr(identity, attribute) for identity in users.values()]
            if len(values) != len(set(values)):
                raise RuntimeError(
                    f"Runtime state contains duplicate {attribute.upper()} mappings."
                )
        return cls(
            users=users, fingerprint=data.get("fingerprint"), pending=data.get("pending", False)
        )

    @property
    def uid_map(self) -> dict[str, int]:
        """Return a legacy username-to-UID map.

        Returns
        -------
        dict[str, int]
            Mapping of username to UID.
        """
        return {username: state.uid for username, state in self.users.items()}

    def save(self, path: Path) -> None:
        """Save runtime state atomically.

        Parameters
        ----------
        path
            Destination state file path.
        """
        atomic_json(
            path,
            {
                "version": 2,
                "users": {
                    username: {
                        "uid": user_state.uid,
                        "gid": user_state.gid,
                        "disabled": user_state.disabled,
                        "applied_fingerprint": user_state.applied_fingerprint,
                    }
                    for username, user_state in sorted(self.users.items())
                },
                "fingerprint": self.fingerprint,
                "pending": self.pending,
            },
        )


@dataclass(frozen=True)
class ResolvedUser:
    """Provider user resolved to concrete UID/GID values."""

    spec: SFTPUser
    uid: int
    gid: int
    policy_at: datetime | None = None


@dataclass(frozen=True)
class RuntimeAction:
    """Planned runtime action for one user."""

    action: Literal["create", "update", "disable"]
    username: str
    uid: int | None = None
    gid: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class RuntimePlan:
    """Runtime synchronization plan."""

    fingerprint: str
    actions: list[RuntimeAction]
    resolved_users: list[ResolvedUser]

    @property
    def changed(self) -> bool:
        """Return whether the plan contains actions.

        Returns
        -------
        bool
            ``True`` when the runtime should be changed.
        """
        return bool(self.actions)

    def summary(self) -> str:
        """Return a concise plan summary.

        Returns
        -------
        str
            Human-readable action counts.
        """
        if not self.actions:
            return "No user changes detected."
        counts = {"create": 0, "update": 0, "disable": 0}
        for action in self.actions:
            counts[action.action] += 1
        return (
            f"Plan: create={counts['create']}, update={counts['update']}, "
            f"disable={counts['disable']}."
        )


def parse_state_users(data: dict[str, Any]) -> dict[str, RuntimeUserState]:
    """Parse current or legacy runtime state user data.

    Parameters
    ----------
    data
        Raw state JSON mapping.

    Returns
    -------
    dict[str, RuntimeUserState]
        Parsed user state mapping.
    """
    if isinstance(data.get("users"), dict):
        return {
            str(username): RuntimeUserState(
                uid=int(values["uid"]),
                gid=int(values.get("gid", values["uid"])),
                disabled=bool(values.get("disabled", False)),
                applied_fingerprint=values.get("applied_fingerprint"),
            )
            for username, values in data["users"].items()
        }
    return {
        str(username): RuntimeUserState(uid=int(uid), gid=int(uid))
        for username, uid in data.get("uid_map", {}).items()
    }


def run_command(args: list[str]) -> None:
    """Run a runtime system command.

    Parameters
    ----------
    args
        Command arguments.
    """
    run_checked(
        args,
        error_type=RuntimeError,
        message=f"Command failed: {' '.join(args)}",
        fallback_suggestion="Inspect container logs.",
    )


def chown_path(path: str | Path, uid: int, gid: int) -> None:
    """Change ownership, failing clearly on platforms without POSIX ownership.

    Parameters
    ----------
    path
        Path whose owner should be changed.
    uid
        Target UID.
    gid
        Target GID.
    """
    chown = getattr(os, "chown", None)
    if chown is None:
        raise RuntimeError(
            "Linux file ownership changes are not available on this platform.",
            suggestion="Run runtime user refresh inside the Linux OpenSSH container.",
        )
    chown(path, uid, gid)


def yes_no(value: bool) -> str:
    """Render a boolean as an OpenSSH yes/no value.

    Parameters
    ----------
    value
        Boolean value.

    Returns
    -------
    str
        ``"yes"`` or ``"no"``.
    """
    return "yes" if value else "no"


def render_sshd_config_text(config: SFTPWardenConfig) -> str:
    """Render OpenSSH server configuration.

    Parameters
    ----------
    config
        Project config.

    Returns
    -------
    str
        ``sshd_config`` text.
    """
    password_auth = yes_no(config.auth.allow_password)
    public_key_auth = yes_no(config.auth.allow_public_key)
    from sftpwarden.runtime.host_keys import KEY_TYPES

    available = [name for name in KEY_TYPES if (Path(config.server.host_keys_dir) / name).is_file()]
    selected = available or ["ssh_host_ed25519_key", "ssh_host_rsa_key"]
    host_keys = "\n".join(f"HostKey {config.server.host_keys_dir}/{name}" for name in selected)
    return f"""Port 22
Protocol 2
{host_keys}
PidFile /run/sshd.pid
PermitRootLogin no
PermitEmptyPasswords no
PasswordAuthentication {password_auth}
PubkeyAuthentication {public_key_auth}
AuthorizedKeysFile /etc/sftpwarden/authorized_keys/%u
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
AllowStreamLocalForwarding no
GatewayPorts no
PermitUserEnvironment no
PrintMotd no
Subsystem sftp internal-sftp

Match Group {config.server.group}
  ChrootDirectory {config.server.data_dir}/%u
  ForceCommand internal-sftp
  PasswordAuthentication {password_auth}
  PubkeyAuthentication {public_key_auth}
  PermitTunnel no
  AllowAgentForwarding no
  AllowTcpForwarding no
  X11Forwarding no
"""


def render_sshd_config(config: SFTPWardenConfig, path: Path = SSHD_CONFIG_PATH) -> None:
    """Write the OpenSSH server configuration.

    Parameters
    ----------
    config
        Project config.
    path
        Destination sshd config path.
    """
    path.write_text(render_sshd_config_text(config), encoding="utf-8")
    os.chmod(path, 0o644)


def state_path(config: SFTPWardenConfig) -> Path:
    """Return the runtime state path for a config.

    Parameters
    ----------
    config
        Project config.

    Returns
    -------
    Path
        Runtime state file path.
    """
    return Path(config.server.state_dir) / "state.json"


def validate_runtime_users(config: SFTPWardenConfig, users: ProviderUsers) -> None:
    """Validate structural input without rejecting effective deny policies.

    Parameters
    ----------
    config
        Project config.
    users
        Provider users.

    """
    ProviderUsers.model_validate(users.model_dump())


def effective_users(config: SFTPWardenConfig, users: ProviderUsers, now: datetime) -> ProviderUsers:
    """Freeze the effective authentication policy at one UTC planning instant."""
    effective = []
    for user in users.users:
        keys = user.active_keys(now) if config.auth.allow_public_key and not user.disabled else []
        password = user.password_hash if config.auth.allow_password and not user.disabled else None
        effective.append(
            user.model_copy(update={"keys": keys, "public_keys": [], "password_hash": password})
        )
    return ProviderUsers(schema_version=users.schema_version, users=effective)


def has_usable_password_hash(user: SFTPUser) -> bool:
    """Return whether a user has an active password hash.

    Parameters
    ----------
    user
        Provider user.

    Returns
    -------
    bool
        ``True`` when the password hash can be used for login.
    """
    return bool(user.password_hash and not user.password_hash.startswith(("!", "*")))


def validate_explicit_ids(users: ProviderUsers) -> None:
    """Validate that explicit provider UID/GID values are unique.

    Parameters
    ----------
    users
        Provider users to inspect.

    Raises
    ------
    RuntimeError
        If two users declare the same explicit UID or GID.
    """
    explicit_uids: dict[int, str] = {}
    explicit_gids: dict[int, str] = {}
    for user in users.users:
        if user.uid is not None and user.uid in explicit_uids:
            raise RuntimeError(
                f"UID {user.uid} is assigned to both {explicit_uids[user.uid]} and {user.username}."
            )
        if user.gid is not None and user.gid in explicit_gids:
            raise RuntimeError(
                f"GID {user.gid} is assigned to both {explicit_gids[user.gid]} and {user.username}."
            )
        if user.uid is not None:
            explicit_uids[user.uid] = user.username
        if user.gid is not None:
            explicit_gids[user.gid] = user.username


def used_identity_ids(users: ProviderUsers, state: RuntimeState) -> set[int]:
    """Collect identity values that cannot be reused during allocation.

    Parameters
    ----------
    users
        Desired provider users.
    state
        Previous runtime state.

    Returns
    -------
    set[int]
        UID/GID values already reserved by provider data or preserved state.
    """
    used_ids = {user_state.uid for user_state in state.users.values()}
    used_ids.update(user_state.gid for user_state in state.users.values())
    if pwd is not None:
        used_ids.update(account.pw_uid for account in pwd.getpwall())
    if grp is not None:
        used_ids.update(group.gr_gid for group in grp.getgrall())
    used_ids.update(user.uid for user in users.users if user.uid is not None)
    used_ids.update(user.gid for user in users.users if user.gid is not None)
    return used_ids


def validate_reserved_id_claims(users: ProviderUsers, state: RuntimeState) -> None:
    """Reject explicit claims on another user's retained UID or GID independently."""
    for field in ("uid", "gid"):
        reserved = {getattr(identity, field): name for name, identity in state.users.items()}
        for user in users.users:
            requested = getattr(user, field)
            if requested in reserved and reserved[requested] != user.username:
                raise RuntimeError(
                    f"{field.upper()} {requested} is reserved by retained user "
                    f"{reserved[requested]}.",
                    suggestion="Preserve retained identities and data; do not recycle their IDs.",
                )


def allocate_uid(
    user: SFTPUser,
    existing_state: RuntimeUserState | None,
    *,
    next_id: int,
    used_ids: set[int],
    max_id: int,
) -> tuple[int, int]:
    """Resolve a user's UID and advance the allocation cursor.

    Parameters
    ----------
    user
        Provider user being allocated.
    existing_state
        Previously saved runtime state for the same username, if any.
    next_id
        Next candidate UID/GID value.
    used_ids
        Identity values already reserved in this plan.
    max_id
        Highest allowed UID/GID value.

    Returns
    -------
    tuple[int, int]
        Resolved UID and next allocation cursor.
    """
    uid = user.uid or (existing_state.uid if existing_state else None)
    if uid is not None:
        return uid, next_id
    while next_id in used_ids:
        next_id += 1
    if next_id > max_id:
        raise RuntimeError("No UID/GID values remain in the configured allocation range.")
    used_ids.add(next_id)
    return next_id, next_id


def resolved_gid(user: SFTPUser, existing_state: RuntimeUserState | None, uid: int) -> int:
    """Resolve the runtime GID for a provider user.

    Parameters
    ----------
    user
        Provider user being resolved.
    existing_state
        Previously saved runtime state for the same username, if any.
    uid
        Resolved UID to reuse as default GID.

    Returns
    -------
    int
        Explicit, preserved, or UID-derived GID.
    """
    return user.gid or (existing_state.gid if existing_state else uid)


def assert_unique_resolved_uid(uid: int, seen_ids: set[int]) -> None:
    """Ensure an allocated UID is unique within the current plan.

    Parameters
    ----------
    uid
        Resolved UID to validate.
    seen_ids
        UIDs already emitted by this plan.
    """
    if uid in seen_ids:
        raise RuntimeError(f"Duplicate UID after allocation: {uid}")
    seen_ids.add(uid)


def allocate_users(
    config: SFTPWardenConfig, users: ProviderUsers, state: RuntimeState
) -> list[ResolvedUser]:
    """Resolve provider users to concrete UID/GID values.

    Parameters
    ----------
    config
        Project config.
    users
        Provider users.
    state
        Mutable runtime state used for preserving allocations.

    Returns
    -------
    list[ResolvedUser]
        Users with resolved identities.
    """
    validate_explicit_ids(users)
    validate_reserved_id_claims(users, state)
    used_ids = used_identity_ids(users, state)
    next_id = config.uid_gid.start
    resolved: list[ResolvedUser] = []
    seen_ids: set[int] = set()
    for user in users.users:
        existing_state = state.users.get(user.username)
        uid, next_id = allocate_uid(
            user,
            existing_state,
            next_id=next_id,
            used_ids=used_ids,
            max_id=config.uid_gid.end,
        )
        gid = resolved_gid(user, existing_state, uid)
        assert_unique_resolved_uid(uid, seen_ids)
        state.users[user.username] = RuntimeUserState(uid=uid, gid=gid, disabled=user.disabled)
        resolved.append(ResolvedUser(spec=user, uid=uid, gid=gid))
    return resolved


def copy_runtime_state(state: RuntimeState) -> RuntimeState:
    """Create a detached copy of runtime state.

    Parameters
    ----------
    state
        Runtime state to copy.

    Returns
    -------
    RuntimeState
        Independent runtime state instance.
    """
    return RuntimeState(
        users={
            username: RuntimeUserState(
                uid=user_state.uid,
                gid=user_state.gid,
                disabled=user_state.disabled,
                applied_fingerprint=user_state.applied_fingerprint,
            )
            for username, user_state in state.users.items()
        },
        fingerprint=state.fingerprint,
        pending=state.pending,
    )


def missing_user_actions(
    config: SFTPWardenConfig, state: RuntimeState, desired_names: set[str], *, force: bool = False
) -> list[RuntimeAction]:
    """Build disable actions for users missing from the provider.

    Parameters
    ----------
    config
        Project config that controls missing-user behavior.
    state
        Existing runtime state.
    desired_names
        Usernames present in the provider.

    Returns
    -------
    list[RuntimeAction]
        Disable actions, or an empty list when the feature is disabled.
    """
    if not config.sync.disable_missing_users:
        return []
    return [
        RuntimeAction(
            action="disable",
            username=username,
            uid=user_state.uid,
            gid=user_state.gid,
            reason="missing from provider",
        )
        for username, user_state in state.users.items()
        if username not in desired_names and (force or not user_state.disabled)
    ]


def runtime_action_for_user(
    resolved: ResolvedUser, previous: RuntimeUserState | None
) -> RuntimeAction:
    """Create the runtime action for one resolved provider user.

    Parameters
    ----------
    resolved
        Provider user with concrete UID/GID values.
    previous
        Previous runtime state for the same username, if any.

    Returns
    -------
    RuntimeAction
        Create, update, or disable action.
    """
    username = resolved.spec.username
    if resolved.spec.disabled:
        return RuntimeAction(
            action="disable",
            username=username,
            uid=resolved.uid,
            gid=resolved.gid,
            reason="disabled in provider",
        )
    if previous is None:
        return RuntimeAction(
            action="create",
            username=username,
            uid=resolved.uid,
            gid=resolved.gid,
            reason="new provider user",
        )
    return RuntimeAction(
        action="update",
        username=username,
        uid=resolved.uid,
        gid=resolved.gid,
        reason=runtime_update_reason(previous, resolved),
    )


def runtime_update_reason(previous: RuntimeUserState, resolved: ResolvedUser) -> str:
    """Explain why an existing runtime user needs an update.

    Parameters
    ----------
    previous
        Previous runtime state for the user.
    resolved
        Desired runtime user.

    Returns
    -------
    str
        Short user-facing reason.
    """
    if previous.uid != resolved.uid or previous.gid != resolved.gid:
        return "uid/gid changed"
    if previous.disabled:
        return "reenable disabled user"
    return "provider changed"


def applied_user_fingerprint(resolved: ResolvedUser) -> str:
    """Fingerprint effective policy with the concrete retained identity."""
    user = resolved.spec.model_copy(update={"uid": resolved.uid, "gid": resolved.gid})
    # Per-user checkpoints are new in 1.4.0. Keep the existing whole-provider
    # fingerprint unchanged, without paying for a YAML serializer per account.
    canonical = json.dumps(runtime_user_data(user), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_runtime_plan(
    config: SFTPWardenConfig,
    users: ProviderUsers,
    state: RuntimeState,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> RuntimePlan:
    """Build a plan for synchronizing runtime users.

    Parameters
    ----------
    config
        Project config.
    users
        Desired provider users.
    state
        Current runtime state.
    force
        Whether to plan updates even when the fingerprint matches.
    now
        Optional injected UTC policy clock.

    Returns
    -------
    RuntimePlan
        Planned runtime actions.
    """
    validate_runtime_users(config, users)
    reference = now or datetime.now(UTC)
    users = effective_users(config, users, reference)
    fingerprint = users_fingerprint(users)
    planning_state = copy_runtime_state(state)
    resolved_users = [
        ResolvedUser(user.spec, user.uid, user.gid, reference)
        for user in allocate_users(config, users, planning_state)
    ]
    desired_names = {resolved.spec.username for resolved in resolved_users}

    # A failed pass may have changed the OS even if the provider was reverted.
    force = force or state.pending
    if not force and state.fingerprint == fingerprint:
        actions = missing_user_actions(config, state, desired_names)
        return RuntimePlan(fingerprint=fingerprint, actions=actions, resolved_users=resolved_users)

    actions = [
        runtime_action_for_user(resolved, state.users.get(resolved.spec.username))
        for resolved in resolved_users
        if force
        or (previous := state.users.get(resolved.spec.username)) is None
        or previous.disabled != resolved.spec.disabled
        or previous.applied_fingerprint != applied_user_fingerprint(resolved)
    ]
    actions.extend(missing_user_actions(config, state, desired_names, force=force))

    return RuntimePlan(fingerprint=fingerprint, actions=actions, resolved_users=resolved_users)


def ensure_group(name: str, gid: int | None = None) -> None:
    """Ensure a system group exists.

    Parameters
    ----------
    name
        Group name.
    gid
        Optional GID.
    """
    if grp is None:
        raise RuntimeError("Runtime group lookup requires the Linux container.")
    try:
        existing = grp.getgrnam(name)
    except KeyError:
        existing = None
    if existing is not None:
        if gid is not None and existing.gr_gid != gid:
            raise RuntimeError(f"Group {name} does not have the required GID {gid}.")
        return
    args = ["groupadd"]
    if gid is not None:
        args.extend(["-g", str(gid)])
    args.append(name)
    run_command(args)


def validate_system_accounts(resolved_users: list[ResolvedUser], state: RuntimeState) -> None:
    """Reject unmanaged/system accounts and ownership changes before OS mutations."""
    if pwd is None or grp is None:
        raise RuntimeError("Runtime account validation requires the Linux container.")
    for resolved in resolved_users:
        username = resolved.spec.username
        previous = state.users.get(username)
        if previous is not None and (previous.uid, previous.gid) != (resolved.uid, resolved.gid):
            raise RuntimeError(
                f"Cannot change retained UID/GID for {username} during refresh.",
                suggestion="Preserve the identity; migrate ownership during explicit maintenance.",
            )
        try:
            account = pwd.getpwnam(username)
        except KeyError:
            account = None
        if account is not None and (
            previous is None
            or account.pw_uid < 1000
            or (account.pw_uid, account.pw_gid) != (resolved.uid, resolved.gid)
        ):
            raise RuntimeError(f"Account {username} is not a matching managed SFTPWarden account.")
        try:
            owner = pwd.getpwuid(resolved.uid)
        except KeyError:
            owner = None
        if owner is not None and owner.pw_name != username:
            raise RuntimeError(f"UID {resolved.uid} is already reserved by a system account.")
        try:
            group = grp.getgrgid(resolved.gid)
        except KeyError:
            group = None
        if group is not None and group.gr_name != f"{username}_sftp":
            raise RuntimeError(f"GID {resolved.gid} is already reserved by another group.")
        try:
            named_group = grp.getgrnam(f"{username}_sftp")
        except KeyError:
            named_group = None
        if named_group is not None and named_group.gr_gid != resolved.gid:
            raise RuntimeError(f"Group {username}_sftp has a conflicting GID.")


def validate_retained_data(config: SFTPWardenConfig, state: RuntimeState) -> None:
    """Require authoritative identity state before reusing retained user data."""
    root = Path(config.server.data_dir)
    if root.exists():
        for child in root.iterdir():
            if child.name not in state.users:
                raise RuntimeError(
                    "Runtime data contains an identity absent from managed state.",
                    suggestion="Restore matching state.json before refresh; retain the data.",
                )


def user_exists(username: str) -> bool:
    """Return whether a system user exists.

    Parameters
    ----------
    username
        Username to check.

    Returns
    -------
    bool
        ``True`` when the user exists.
    """
    if pwd is None:
        raise RuntimeError(
            "POSIX user lookup is not available on this platform.",
            suggestion="Run runtime user refresh inside the Linux OpenSSH container.",
        )
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def account_password(config: SFTPWardenConfig, user: SFTPUser) -> str:
    """Return the final shadow value without transiently enabling a credential."""
    if user.disabled:
        return DISABLED_PASSWORD_HASH
    if has_usable_password_hash(user) and config.auth.allow_password:
        return user.password_hash or NO_PASSWORD_HASH
    return NO_PASSWORD_HASH


def ensure_system_user(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
    """Create or update the system account for a resolved user.

    Parameters
    ----------
    config
        Project config.
    resolved
        Resolved provider user.
    """
    user = resolved.spec
    primary_group = f"{user.username}_sftp"
    ensure_group(primary_group, resolved.gid)
    ensure_group(config.server.group)
    shell = "/sbin/nologin" if Path("/sbin/nologin").exists() else "/usr/sbin/nologin"
    password = account_password(config, user)
    # Set the final shadow value in the account operation itself: no transient
    # unlock and no repeated rewrite of the complete shadow database per user.
    if user_exists(user.username):
        run_command(
            [
                "usermod",
                "-u",
                str(resolved.uid),
                "-g",
                primary_group,
                "-G",
                config.server.group,
                "-s",
                shell,
                "-p",
                password,
                user.username,
            ]
        )
    else:
        run_command(
            [
                "useradd",
                "-u",
                str(resolved.uid),
                "-g",
                primary_group,
                "-G",
                config.server.group,
                "-M",
                "-d",
                f"{config.server.data_dir}/{user.username}",
                "-s",
                shell,
                "-p",
                password,
                user.username,
            ]
        )


def create_locked_accounts(config: SFTPWardenConfig, users: list[ResolvedUser]) -> None:
    """Create new identities locked, then append their shared group membership once.

    The caller reserves identities and prepares safe chroots and key files first.
    Shadow's own account tools retain their database locking and error handling.
    The image's newusers PAM policy leaves shadow passwords locked. Credentials
    are applied separately, after all accounts and group memberships are ready.
    """
    values = [(user.spec.username, account_password(config, user.spec)) for user in users]
    shell = "/sbin/nologin" if Path("/sbin/nologin").exists() else "/usr/sbin/nologin"
    records = [
        (
            resolved.spec.username,
            DISABLED_PASSWORD_HASH,
            str(resolved.uid),
            f"{resolved.spec.username}_sftp",
            "",
            f"{config.server.data_dir}/{resolved.spec.username}",
            shell,
        )
        for resolved in users
    ]
    if any(
        character in value
        for record in [*values, *records]
        for value in record
        for character in ":\r\n\0"
    ):
        raise RuntimeError("Account values contain an invalid record separator.")
    for resolved in users:
        ensure_group(f"{resolved.spec.username}_sftp", resolved.gid)
    run_checked(
        ["newusers"],
        input_text="".join(":".join(record) + "\n" for record in records),
        error_type=RuntimeError,
        message="Could not create locked accounts.",
        fallback_suggestion="Retry reconciliation; pending identities remain reserved.",
        private_output=True,
    )
    verify_locked_accounts([name for name, _ in values])
    # Bound argv size even for inventories larger than the reference workload.
    for offset in range(0, len(users), 256):
        names = ",".join(user.spec.username for user in users[offset : offset + 256])
        run_command(["groupmod", "--append", "--users", names, config.server.group])
    passwords = "".join(f"{name}:{password}\n" for name, password in values)
    run_checked(
        ["chpasswd", "--encrypted"],
        input_text=passwords,
        error_type=RuntimeError,
        message="Could not apply new account authentication policy.",
        fallback_suggestion="Retry reconciliation; pending identities remain reserved.",
        private_output=True,
    )


def verify_locked_accounts(names: list[str]) -> None:
    """Refuse a provisioning-tool policy that could activate temporary credentials."""
    passwords = {
        fields[0]: fields[1]
        for line in Path("/etc/shadow").read_text(encoding="utf-8").splitlines()
        if len(fields := line.split(":")) >= 2
    }
    if any(passwords.get(name) != DISABLED_PASSWORD_HASH for name in names):
        raise RuntimeError("New accounts were not created locked; check the runtime image policy.")


def ensure_directories(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
    """Create and permission chroot/upload directories for a user.

    Parameters
    ----------
    config
        Project config.
    resolved
        Resolved provider user.
    """
    ensure_upload_tree(
        Path(config.server.data_dir),
        resolved.spec.username,
        resolved.spec.upload_dir,
        uid=resolved.uid,
        gid=resolved.gid,
        root_mode=int(config.isolation.root_permissions, 8),
        upload_mode=int(config.isolation.upload_permissions, 8),
    )


def write_authorized_keys(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
    """Write restricted authorized keys for a user.

    Parameters
    ----------
    config
        Project config.
    resolved
        Resolved provider user.
    """
    auth_dir = Path("/etc/sftpwarden/authorized_keys")
    auth_dir.mkdir(parents=True, exist_ok=True)
    path = auth_dir / resolved.spec.username
    key_options = "restrict"
    keys = (
        resolved.spec.active_keys(resolved.policy_at)
        if config.auth.allow_public_key and not resolved.spec.disabled
        else []
    )
    # The plan freezes activity once; OpenSSH independently enforces the deadline.
    lines = [authorized_key_line(key_options, key) for key in keys if not key.disabled]
    fd, temporary_name = tempfile.mkstemp(prefix=f".{resolved.spec.username}-", dir=auth_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + ("\n" if lines else ""))
            stream.flush()
            chown_path(temporary, 0, 0)
            os.chmod(temporary, 0o644)
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(auth_dir)
    finally:
        temporary.unlink(missing_ok=True)


def authorized_key_line(key_options: str, key: SFTPUserKey) -> str:
    """Render one restricted authorized_keys line."""
    if key.expires_at is not None:
        # OpenSSH rejects when now > expiry-time (inclusive last valid second).
        # Our policy rejects at expires_at; never extend access into that second.
        deadline = (key.expires_at.astimezone(UTC) - timedelta(seconds=1)).strftime("%Y%m%d%H%M%SZ")
        key_options += f',expiry-time="{deadline}"'
    key_type, key_blob = key.public_key.split()[:2]
    comment_parts = []
    if key.name:
        comment_parts.append(f"sftpwarden:key={key.name}")
    if key.fingerprint:
        comment_parts.append(f"fingerprint={key.fingerprint}")
    comment = " ".join(comment_parts)
    return f"{key_options} {key_type} {key_blob} {comment}".rstrip()


def disable_missing(
    config: SFTPWardenConfig, desired: ProviderUsers, state: RuntimeState, force: bool = False
) -> None:
    """Disable system users missing from the provider.

    Parameters
    ----------
    config
        Project config.
    desired
        Desired provider users.
    state
        Mutable runtime state.
    """
    if not config.sync.disable_missing_users:
        return
    desired_names = {user.username for user in desired.users}
    for username, user_state in state.users.items():
        if username not in desired_names and (force or not user_state.disabled):
            write_authorized_keys(
                config,
                ResolvedUser(
                    SFTPUser(username=username, disabled=True), user_state.uid, user_state.gid
                ),
            )
            if user_exists(username):
                run_command(["usermod", "-p", DISABLED_PASSWORD_HASH, username])
            state.users[username] = RuntimeUserState(
                uid=user_state.uid, gid=user_state.gid, disabled=True
            )


def load_runtime_inputs(
    config_path: str | Path,
) -> tuple[SFTPWardenConfig, ProviderUsers, RuntimeState]:
    """Load runtime config, provider users, and state.

    Parameters
    ----------
    config_path
        Project config path inside the runtime.

    Returns
    -------
    tuple[SFTPWardenConfig, ProviderUsers, RuntimeState]
        Runtime inputs.
    """
    config = load_config(config_path)
    provider_path = Path(config.provider.path)
    users = load_users(
        config.provider.type,
        provider_path,
        dsn=config.provider.dsn,
        query=config.provider.query,
        table=config.provider.table,
        collection=config.provider.collection,
        user_schema=config.provider.user_schema,
        require_schema_match=True,
    )
    state = RuntimeState.load(state_path(config))
    return config, users, state


def apply_once(config_path: str | Path = CONTAINER_CONFIG_PATH, *, force: bool = False) -> str:
    """Apply one runtime synchronization pass.

    Parameters
    ----------
    config_path
        Runtime config path.
    force
        Whether to force user update planning.

    Returns
    -------
    str
        Result summary.
    """
    config = load_config(config_path)
    directory = Path(config.server.state_dir)
    with runtime_lock(directory):
        generation = read_json(directory / "lifecycle.json").get("generation")
        status = read_json(directory / "sync-status.json")
        if status.get("generation") != generation:
            status = {}
        if status.get("pending"):
            status["failure"] = "InterruptedAttempt"
        status.update(
            {
                "version": 1,
                "generation": generation,
                "attempt_pid": os.getpid(),
                "last_attempt": datetime.now(UTC).isoformat(),
                "attempt_monotonic": time.monotonic(),
                "pending": True,
            }
        )
        atomic_json(directory / "sync-status.json", status)
        try:
            result = _apply_once(config_path, force=force)
        except Exception as exc:
            status.update({"pending": False, "failure": type(exc).__name__})
            atomic_json(directory / "sync-status.json", status)
            raise
        status.update(
            {
                "pending": False,
                "failure": None,
                "last_success": datetime.now(UTC).isoformat(),
                "success_monotonic": time.monotonic(),
            }
        )
        atomic_json(directory / "sync-status.json", status)
        return result


def _apply_once(config_path: str | Path, *, force: bool) -> str:
    """Reconcile while the caller owns the complete runtime state transaction."""
    config, users, state = load_runtime_inputs(config_path)
    state_file = state_path(config)
    validate_retained_data(config, state)
    plan = build_runtime_plan(config, users, state, force=force)
    if not plan.changed:
        if state.fingerprint != plan.fingerprint:
            state.fingerprint = plan.fingerprint
            state.save(state_file)
        return "No user changes detected."
    validate_system_accounts(plan.resolved_users, state)
    # Reserve before provisioning so an interrupted create can safely recover.
    # Keep the previous fingerprint until all required effects succeed.
    repair = force or state.pending
    for resolved in plan.resolved_users:
        if resolved.spec.username not in state.users:
            # An interrupted grant is not proof that access was disabled.
            state.users[resolved.spec.username] = RuntimeUserState(resolved.uid, resolved.gid)
    # Persist incomplete application before the first OS effect, including updates.
    state.pending = True
    state.save(state_file)
    ensure_group(config.server.group)
    changed_names = {action.username for action in plan.actions}
    new_accounts = [
        resolved
        for resolved in plan.resolved_users
        if resolved.spec.username in changed_names and not user_exists(resolved.spec.username)
    ]
    batched = (
        {resolved.spec.username for resolved in new_accounts} if len(new_accounts) > 1 else set()
    )
    if batched:
        for resolved in new_accounts:
            write_authorized_keys(config, resolved)
            ensure_directories(config, resolved)
        create_locked_accounts(config, new_accounts)
    for resolved in plan.resolved_users:
        if resolved.spec.username not in changed_names:
            continue
        # Remove old keys before account/password operations can grant access.
        if resolved.spec.username not in batched:
            write_authorized_keys(config, resolved)
            ensure_system_user(config, resolved)
            ensure_directories(config, resolved)
        state.users[resolved.spec.username] = RuntimeUserState(
            uid=resolved.uid,
            gid=resolved.gid,
            disabled=resolved.spec.disabled,
            applied_fingerprint=applied_user_fingerprint(resolved),
        )
    disable_missing(config, users, state, repair)
    state.fingerprint = plan.fingerprint
    state.pending = False
    state.save(state_file)
    return f"Applied {len(plan.actions)} action(s): {plan.summary()}"


def run_sync_loop(config_path: str | Path = CONTAINER_CONFIG_PATH) -> None:
    """Run the runtime synchronization loop forever.

    Parameters
    ----------
    config_path
        Runtime config path.
    """
    from sftpwarden.runtime.configuration import load_runtime_config
    from sftpwarden.utils.event_log import service_event

    config = load_runtime_config(config_path)
    delay = 1
    while True:
        started = time.monotonic()
        try:
            observed = provider_stamp(config)
            if config.sync.enabled:
                result = execute_pass(config_path)
                if result != "No user changes detected.":
                    service_event(
                        config.logging, "info", "Runtime policy reconciliation completed."
                    )
            delay = 1
            # The interval is measured between starts, not added to a slow pass.
            # Passes remain serial; an overrun never starts a concurrent worker.
            wait_for_provider_change(
                config,
                observed,
                max(0, config.sync.interval_seconds - (time.monotonic() - started)),
            )
        except Exception:  # noqa: BLE001
            # Provider errors may contain credentials. Status stores only the category.
            service_event(
                config.logging, "error", "Runtime synchronization failed; retrying pending policy."
            )
            time.sleep(delay)
            delay = min(delay * 2, RETRY_CAP_SECONDS)
