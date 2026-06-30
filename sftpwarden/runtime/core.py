from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

try:
    import pwd
except ModuleNotFoundError:
    pwd = None

from sftpwarden.config import SFTPWardenConfig, load_config
from sftpwarden.providers import ProviderUsers, SFTPUser, load_users, users_fingerprint
from sftpwarden.system.commands import run, run_checked
from sftpwarden.utils.console import console
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.errors import RuntimeError

SSHD_CONFIG_PATH = Path("/etc/ssh/sshd_config")
DISABLED_PASSWORD_HASH = "!"
NO_PASSWORD_HASH = "*"


@dataclass
class RuntimeUserState:
    """Persisted runtime identity state for one user."""

    uid: int
    gid: int
    disabled: bool = False


@dataclass
class RuntimeState:
    """Persisted runtime state for all managed users."""

    users: dict[str, RuntimeUserState]
    fingerprint: str | None = None

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
        return cls(users=parse_state_users(data), fingerprint=data.get("fingerprint"))

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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "version": 2,
                    "users": {
                        username: {
                            "uid": user_state.uid,
                            "gid": user_state.gid,
                            "disabled": user_state.disabled,
                        }
                        for username, user_state in sorted(self.users.items())
                    },
                    "fingerprint": self.fingerprint,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)


@dataclass(frozen=True)
class ResolvedUser:
    """Provider user resolved to concrete UID/GID values."""

    spec: SFTPUser
    uid: int
    gid: int


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
    return f"""Port 22
Protocol 2
HostKey {config.server.host_keys_dir}/ssh_host_ed25519_key
HostKey {config.server.host_keys_dir}/ssh_host_rsa_key

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
    """Validate that active users can authenticate.

    Parameters
    ----------
    config
        Project config.
    users
        Provider users.

    Raises
    ------
    RuntimeError
        Raised when an active user has no enabled auth method.
    """
    for user in users.users:
        if user.disabled:
            continue
        has_password = has_usable_password_hash(user)
        has_key = bool(user.public_keys)
        if has_password and config.auth.allow_password:
            continue
        if has_key and config.auth.allow_public_key:
            continue
        raise RuntimeError(
            f"User {user.username} has no enabled authentication method.",
            suggestion=(
                "Add a password hash, add a public key, or enable the matching auth method "
                "in sftpwarden.yaml."
            ),
        )


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
    provider_names = {user.username for user in users.users}
    used_ids = {
        user_state.uid for username, user_state in state.users.items() if username in provider_names
    }
    used_ids.update(user.uid for user in users.users if user.uid is not None)
    used_ids.update(user.gid for user in users.users if user.gid is not None)
    return used_ids


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
            )
            for username, user_state in state.users.items()
        },
        fingerprint=state.fingerprint,
    )


def missing_user_actions(
    config: SFTPWardenConfig, state: RuntimeState, desired_names: set[str]
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
        if username not in desired_names
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


def build_runtime_plan(
    config: SFTPWardenConfig,
    users: ProviderUsers,
    state: RuntimeState,
    *,
    force: bool = False,
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

    Returns
    -------
    RuntimePlan
        Planned runtime actions.
    """
    validate_runtime_users(config, users)
    fingerprint = users_fingerprint(users)
    planning_state = copy_runtime_state(state)
    resolved_users = allocate_users(config, users, planning_state)
    desired_names = {resolved.spec.username for resolved in resolved_users}

    if not force and state.fingerprint == fingerprint:
        actions = missing_user_actions(config, state, desired_names)
        return RuntimePlan(fingerprint=fingerprint, actions=actions, resolved_users=resolved_users)

    actions = [
        runtime_action_for_user(resolved, state.users.get(resolved.spec.username))
        for resolved in resolved_users
    ]
    actions.extend(missing_user_actions(config, state, desired_names))

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
    if (
        shutil.which("getent")
        and run(
            ["getent", "group", name],
            capture_output=True,
        ).returncode
        == 0
    ):
        return
    args = ["groupadd"]
    if gid is not None:
        args.extend(["-g", str(gid)])
    args.append(name)
    run_command(args)


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
        pwd.getpwnam(username)  # type: ignore
        return True
    except KeyError:
        return False


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
                user.username,
            ]
        )
    if has_usable_password_hash(user):
        run_command(["usermod", "-p", user.password_hash or NO_PASSWORD_HASH, user.username])
    else:
        run_command(["usermod", "-p", NO_PASSWORD_HASH, user.username])
    if user.disabled:
        run_command(["usermod", "-p", DISABLED_PASSWORD_HASH, user.username])
    else:
        run_command(["usermod", "-U", user.username])


def ensure_directories(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
    """Create and permission chroot/upload directories for a user.

    Parameters
    ----------
    config
        Project config.
    resolved
        Resolved provider user.
    """
    root = Path(config.server.data_dir) / resolved.spec.username
    upload = root / resolved.spec.upload_dir
    root.mkdir(parents=True, exist_ok=True)
    upload.mkdir(parents=True, exist_ok=True)
    chown_path(root, 0, 0)
    os.chmod(root, int(config.isolation.root_permissions, 8))
    chown_path(upload, resolved.uid, resolved.gid)
    os.chmod(upload, int(config.isolation.upload_permissions, 8))


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
    lines = [f"{key_options} {key}" for key in resolved.spec.public_keys]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    chown_path(path, 0, 0)
    os.chmod(path, 0o644)


def disable_missing(config: SFTPWardenConfig, desired: ProviderUsers, state: RuntimeState) -> None:
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
        if username not in desired_names and user_exists(username):
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
    config, users, state = load_runtime_inputs(config_path)
    render_sshd_config(config)
    state_file = state_path(config)
    plan = build_runtime_plan(config, users, state, force=force)
    if not plan.changed:
        state.fingerprint = plan.fingerprint
        state.save(state_file)
        return "No user changes detected."
    ensure_group(config.server.group)
    for resolved in plan.resolved_users:
        ensure_system_user(config, resolved)
        ensure_directories(config, resolved)
        write_authorized_keys(config, resolved)
        state.users[resolved.spec.username] = RuntimeUserState(
            uid=resolved.uid,
            gid=resolved.gid,
            disabled=resolved.spec.disabled,
        )
    disable_missing(config, users, state)
    state.fingerprint = plan.fingerprint
    state.save(state_file)
    return f"Applied {len(plan.actions)} action(s): {plan.summary()}"


def run_sync_loop(config_path: str | Path = CONTAINER_CONFIG_PATH) -> None:
    """Run the runtime synchronization loop forever.

    Parameters
    ----------
    config_path
        Runtime config path.
    """
    while True:
        config = load_config(config_path)
        if config.sync.enabled:
            result = apply_once(config_path)
            if result != "No user changes detected.":
                console.print(result)
        time.sleep(config.sync.interval_seconds)
