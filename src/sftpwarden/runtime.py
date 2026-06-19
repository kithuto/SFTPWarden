from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sftpwarden.config import SFTPWardenConfig, load_config
from sftpwarden.constants import CONTAINER_CONFIG_PATH
from sftpwarden.errors import RuntimeError
from sftpwarden.providers import ProviderUsers, SFTPUser, load_users, users_fingerprint

SSHD_CONFIG_PATH = Path("/etc/ssh/sshd_config")
DISABLED_PASSWORD_HASH = "!"
NO_PASSWORD_HASH = "*"


@dataclass
class RuntimeUserState:
    uid: int
    gid: int
    disabled: bool = False


@dataclass
class RuntimeState:
    users: dict[str, RuntimeUserState]
    fingerprint: str | None = None

    @classmethod
    def load(cls, path: Path) -> RuntimeState:
        if not path.exists():
            return cls(users={})
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(users=parse_state_users(data), fingerprint=data.get("fingerprint"))

    @property
    def uid_map(self) -> dict[str, int]:
        return {username: state.uid for username, state in self.users.items()}

    def save(self, path: Path) -> None:
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
    spec: SFTPUser
    uid: int
    gid: int


def parse_state_users(data: dict[str, Any]) -> dict[str, RuntimeUserState]:
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
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}",
            suggestion=(result.stderr or result.stdout or "Inspect container logs.").strip(),
        )


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def render_sshd_config_text(config: SFTPWardenConfig) -> str:
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
    path.write_text(render_sshd_config_text(config), encoding="utf-8")
    os.chmod(path, 0o644)


def state_path(config: SFTPWardenConfig) -> Path:
    return Path(config.server.state_dir) / "state.json"


def validate_runtime_users(config: SFTPWardenConfig, users: ProviderUsers) -> None:
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
    return bool(user.password_hash and not user.password_hash.startswith(("!", "*")))


def allocate_users(
    config: SFTPWardenConfig, users: ProviderUsers, state: RuntimeState
) -> list[ResolvedUser]:
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

    used_ids = {
        user_state.uid
        for username, user_state in state.users.items()
        if any(user.username == username for user in users.users)
    }
    used_ids.update(user.uid for user in users.users if user.uid is not None)
    used_ids.update(user.gid for user in users.users if user.gid is not None)

    next_id = config.uid_gid.start
    resolved: list[ResolvedUser] = []
    seen_ids: set[int] = set()
    for user in users.users:
        existing_state = state.users.get(user.username)
        uid = user.uid or (existing_state.uid if existing_state else None)
        if uid is None:
            while next_id in used_ids:
                next_id += 1
            if next_id > config.uid_gid.end:
                raise RuntimeError("No UID/GID values remain in the configured allocation range.")
            uid = next_id
            used_ids.add(uid)
        gid = user.gid or (existing_state.gid if existing_state else uid)
        if uid in seen_ids:
            raise RuntimeError(f"Duplicate UID after allocation: {uid}")
        seen_ids.add(uid)
        state.users[user.username] = RuntimeUserState(uid=uid, gid=gid, disabled=user.disabled)
        resolved.append(ResolvedUser(spec=user, uid=uid, gid=gid))
    return resolved


def ensure_group(name: str, gid: int | None = None) -> None:
    if (
        shutil.which("getent")
        and subprocess.run(
            ["getent", "group", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def ensure_system_user(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
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
        run_command(["usermod", "-p", user.password_hash, user.username]) # type: ignore
    else:
        run_command(["usermod", "-p", NO_PASSWORD_HASH, user.username])
    if user.disabled:
        run_command(["usermod", "-p", DISABLED_PASSWORD_HASH, user.username])
    else:
        run_command(["usermod", "-U", user.username])


def ensure_directories(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
    root = Path(config.server.data_dir) / resolved.spec.username
    upload = root / resolved.spec.upload_dir
    root.mkdir(parents=True, exist_ok=True)
    upload.mkdir(parents=True, exist_ok=True)
    os.chown(root, 0, 0)
    os.chmod(root, int(config.isolation.root_permissions, 8))
    os.chown(upload, resolved.uid, resolved.gid)
    os.chmod(upload, int(config.isolation.upload_permissions, 8))


def write_authorized_keys(config: SFTPWardenConfig, resolved: ResolvedUser) -> None:
    auth_dir = Path("/etc/sftpwarden/authorized_keys")
    auth_dir.mkdir(parents=True, exist_ok=True)
    path = auth_dir / resolved.spec.username
    key_options = "restrict"
    lines = [f"{key_options} {key}" for key in resolved.spec.public_keys]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.chown(path, 0, 0)
    os.chmod(path, 0o644)


def disable_missing(config: SFTPWardenConfig, desired: ProviderUsers, state: RuntimeState) -> None:
    if not config.sync.disable_missing_users:
        return
    desired_names = {user.username for user in desired.users}
    for username, user_state in list(state.users.items()):
        if username not in desired_names and user_exists(username):
            run_command(["usermod", "-p", DISABLED_PASSWORD_HASH, username])
            state.users[username] = RuntimeUserState(
                uid=user_state.uid, gid=user_state.gid, disabled=True
            )


def apply_once(config_path: str | Path = CONTAINER_CONFIG_PATH, *, force: bool = False) -> str:
    config = load_config(config_path)
    provider_path = Path(config.provider.path)
    users = load_users(config.provider.type, provider_path)
    validate_runtime_users(config, users)
    render_sshd_config(config)
    fingerprint = users_fingerprint(users)
    state_file = state_path(config)
    state = RuntimeState.load(state_file)
    if not force and state.fingerprint == fingerprint:
        return "No user changes detected."
    resolved_users = allocate_users(config, users, state)
    ensure_group(config.server.group)
    for resolved in resolved_users:
        ensure_system_user(config, resolved)
        ensure_directories(config, resolved)
        write_authorized_keys(config, resolved)
    disable_missing(config, users, state)
    state.fingerprint = fingerprint
    state.save(state_file)
    return f"Applied {len(resolved_users)} user(s)."


def run_sync_loop(config_path: str | Path = CONTAINER_CONFIG_PATH) -> None:
    while True:
        config = load_config(config_path)
        if config.sync.enabled:
            apply_once(config_path)
        time.sleep(config.sync.interval_seconds)
