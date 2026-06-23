from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.runtime as runtime_module
from sftpwarden.config import ProjectConfig, SFTPWardenConfig, write_config
from sftpwarden.providers import ProviderUsers, SFTPUser
from sftpwarden.runtime import (
    ResolvedUser,
    RuntimeState,
    RuntimeUserState,
    allocate_users,
    apply_once,
    assert_unique_resolved_uid,
    build_runtime_plan,
    disable_missing,
    ensure_directories,
    ensure_group,
    ensure_system_user,
    load_runtime_inputs,
    missing_user_actions,
    parse_state_users,
    render_sshd_config,
    run_command,
    run_sync_loop,
    state_path,
    validate_runtime_users,
    write_authorized_keys,
)
from sftpwarden.users import users_fingerprint
from sftpwarden.utils.errors import RuntimeError

TEST_SHADOW_HASH = "$6$rounds=500000$saltstring$hashvalue"


def user(
    username: str,
    *,
    password_hash: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    public_keys: list[str] | None = None,
    disabled: bool = False,
    comment: str | None = None,
) -> SFTPUser:
    return SFTPUser(
        username=username,
        password_hash=TEST_SHADOW_HASH if password_hash is None else password_hash,
        uid=uid,
        gid=gid,
        public_keys=public_keys or [],
        disabled=disabled,
        comment=comment,
    )


def test_runtime_state_saves_uid_gid_mapping(tmp_path: Path) -> None:
    state = RuntimeState(users={"alice": RuntimeUserState(uid=10000, gid=10001)})
    state_path = tmp_path / "state.json"

    state.save(state_path)
    loaded = RuntimeState.load(state_path)

    assert loaded.users["alice"].uid == 10000
    assert loaded.users["alice"].gid == 10001


def test_runtime_state_migrates_old_uid_map() -> None:
    users = parse_state_users({"uid_map": {"alice": 10000}})

    assert users["alice"] == RuntimeUserState(uid=10000, gid=10000)


def test_runtime_state_loads_missing_file_and_uid_map(tmp_path: Path) -> None:
    state = RuntimeState.load(tmp_path / "missing.json")
    state.users["alice"] = RuntimeUserState(uid=12000, gid=12001)

    assert state.uid_map == {"alice": 12000}


def test_allocate_users_preserves_existing_ids() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    state = RuntimeState(users={"alice": RuntimeUserState(uid=12000, gid=12000)})

    resolved = allocate_users(config, ProviderUsers(users=[user("alice")]), state)

    assert resolved[0].uid == 12000
    assert resolved[0].gid == 12000


def test_allocate_users_rejects_duplicate_explicit_gid() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    state = RuntimeState(users={})

    with pytest.raises(RuntimeError, match="GID 20000"):
        allocate_users(
            config,
            ProviderUsers(users=[user("alice", gid=20000), user("bob", gid=20000)]),
            state,
        )


def test_allocate_users_rejects_duplicate_explicit_uid() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))

    with pytest.raises(RuntimeError, match="UID 20000"):
        allocate_users(
            config,
            ProviderUsers.model_construct(users=[user("alice", uid=20000), user("bob", uid=20000)]),
            RuntimeState(users={}),
        )


def test_allocate_users_skips_used_ids_and_reports_exhaustion() -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "uid_gid": {"start": 10000, "end": 10001},
        }
    )
    users = ProviderUsers(users=[user("reserved", uid=10000), user("alice")])
    state = RuntimeState(users={})

    resolved = allocate_users(config, users, state)

    assert resolved[1].uid == 10001
    with pytest.raises(RuntimeError, match="No UID/GID values remain"):
        allocate_users(
            config,
            ProviderUsers(users=[user("reserved", uid=10000), user("alice"), user("bob")]),
            RuntimeState(users={}),
        )


def test_assert_unique_resolved_uid_reports_duplicates() -> None:
    with pytest.raises(RuntimeError, match="Duplicate UID"):
        assert_unique_resolved_uid(10000, {10000})


def test_runtime_rejects_user_without_enabled_auth_method() -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "auth": {"allow_password": False, "allow_public_key": True},
        }
    )

    with pytest.raises(RuntimeError, match="no enabled authentication method"):
        validate_runtime_users(config, ProviderUsers(users=[user("alice")]))


def test_runtime_rejects_impossible_hash_as_only_active_auth_method() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))

    with pytest.raises(RuntimeError, match="no enabled authentication method"):
        validate_runtime_users(
            config,
            ProviderUsers(users=[user("alice", password_hash="!")]),  # noqa: S106
        )


def test_runtime_allows_disabled_user_with_impossible_hash() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))

    validate_runtime_users(
        config,
        ProviderUsers(users=[user("alice", password_hash="!", disabled=True)]),  # noqa: S106
    )


def test_runtime_allows_public_key_when_password_auth_is_disabled() -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "auth": {"allow_password": False, "allow_public_key": True},
        }
    )

    validate_runtime_users(
        config,
        ProviderUsers(
            users=[
                user(
                    "alice",
                    password_hash=None,
                    public_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"],
                )
            ]
        ),
    )


def test_runtime_plan_creates_new_user() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))

    plan = build_runtime_plan(config, ProviderUsers(users=[user("alice")]), RuntimeState(users={}))

    assert plan.changed
    assert plan.actions[0].action == "create"
    assert plan.actions[0].username == "alice"


def test_runtime_plan_noops_when_fingerprint_matches() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    users = ProviderUsers(users=[user("alice")])
    initial_plan = build_runtime_plan(config, users, RuntimeState(users={}))
    state = RuntimeState(
        users={"alice": RuntimeUserState(uid=10000, gid=10000)},
        fingerprint=initial_plan.fingerprint,
    )

    plan = build_runtime_plan(config, users, state)

    assert not plan.changed
    assert plan.actions == []


def test_users_fingerprint_ignores_comment() -> None:
    first = ProviderUsers(users=[user("alice", comment="Finance dropbox")])
    second = ProviderUsers(users=[user("alice", comment="Old archive account")])

    assert users_fingerprint(first) == users_fingerprint(second)


def test_runtime_plan_force_updates_existing_user() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    users = ProviderUsers(users=[user("alice")])
    initial_plan = build_runtime_plan(config, users, RuntimeState(users={}))
    state = RuntimeState(
        users={"alice": RuntimeUserState(uid=10000, gid=10000)},
        fingerprint=initial_plan.fingerprint,
    )

    plan = build_runtime_plan(config, users, state, force=True)

    assert plan.changed
    assert plan.actions[0].action == "update"


def test_runtime_plan_update_reasons_cover_identity_and_reenable() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))

    identity_change = build_runtime_plan(
        config,
        ProviderUsers(users=[user("alice", uid=12001, gid=12002)]),
        RuntimeState(users={"alice": RuntimeUserState(uid=12000, gid=12000)}),
        force=True,
    )
    reenable = build_runtime_plan(
        config,
        ProviderUsers(users=[user("bob")]),
        RuntimeState(users={"bob": RuntimeUserState(uid=10000, gid=10000, disabled=True)}),
        force=True,
    )
    disabled = build_runtime_plan(
        config,
        ProviderUsers(users=[user("carol", disabled=True)]),
        RuntimeState(users={}),
    )

    assert identity_change.actions[0].reason == "uid/gid changed"
    assert reenable.actions[0].reason == "reenable disabled user"
    assert disabled.actions[0].reason == "disabled in provider"


def test_runtime_plan_disables_missing_user() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    state = RuntimeState(users={"alice": RuntimeUserState(uid=10000, gid=10000)})

    plan = build_runtime_plan(config, ProviderUsers(users=[]), state)

    assert plan.changed
    assert plan.actions[0].action == "disable"
    assert plan.actions[0].reason == "missing from provider"


def test_missing_user_actions_can_be_disabled() -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "sync": {"disable_missing_users": False},
        }
    )

    actions = missing_user_actions(
        config,
        RuntimeState(users={"alice": RuntimeUserState(uid=10000, gid=10000)}),
        set(),
    )

    assert actions == []


def test_render_sshd_config_and_state_path_use_configured_locations(tmp_path: Path) -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "server": {
                "host_keys_dir": "/keys",
                "state_dir": str(tmp_path / "state"),
                "group": "sftpusers",
            },
            "auth": {"allow_password": False, "allow_public_key": True},
        }
    )
    target = tmp_path / "sshd_config"

    render_sshd_config(config, target)

    text = target.read_text(encoding="utf-8")
    assert "PasswordAuthentication no" in text
    assert "PubkeyAuthentication yes" in text
    assert "Match Group sftpusers" in text
    assert (target.stat().st_mode & 0o777) == 0o644
    assert state_path(config) == tmp_path / "state" / "state.json"


def test_ensure_group_skips_existing_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(runtime_module.shutil, "which", lambda command: "/usr/bin/getent")
    monkeypatch.setattr(runtime_module, "run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(runtime_module, "run_command", calls.append)

    ensure_group("sftpusers")

    assert calls == []


def test_ensure_group_creates_missing_group_with_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(runtime_module.shutil, "which", lambda command: None)
    monkeypatch.setattr(runtime_module, "run_command", calls.append)

    ensure_group("alice_sftp", gid=12000)

    assert calls == [["groupadd", "-g", "12000", "alice_sftp"]]


def test_run_command_delegates_to_checked_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], type, str]] = []
    monkeypatch.setattr(
        runtime_module,
        "run_checked",
        lambda args, *, error_type, message, fallback_suggestion: calls.append(
            (args, error_type, message)
        ),
    )

    run_command(["true"])

    assert calls == [(["true"], RuntimeError, "Command failed: true")]


def test_ensure_system_user_creates_account_and_sets_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    resolved = ResolvedUser(spec=user("alice"), uid=12000, gid=12000)
    groups: list[tuple[str, int | None]] = []
    commands: list[list[str]] = []

    monkeypatch.setattr(
        runtime_module, "ensure_group", lambda name, gid=None: groups.append((name, gid))
    )
    monkeypatch.setattr(runtime_module, "user_exists", lambda username: False)
    monkeypatch.setattr(runtime_module, "run_command", commands.append)

    ensure_system_user(config, resolved)

    assert groups == [("alice_sftp", 12000), ("sftpwarden_users", None)]
    assert commands[0][:5] == ["useradd", "-u", "12000", "-g", "alice_sftp"]
    assert ["usermod", "-p", TEST_SHADOW_HASH, "alice"] in commands
    assert ["usermod", "-U", "alice"] in commands


def test_ensure_system_user_updates_and_disables_existing_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    resolved = ResolvedUser(
        spec=user("alice", password_hash="!", disabled=True),  # noqa: S106
        uid=12000,
        gid=12000,
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(runtime_module, "ensure_group", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_module, "user_exists", lambda username: True)
    monkeypatch.setattr(runtime_module, "run_command", commands.append)

    ensure_system_user(config, resolved)

    assert commands[0][:5] == ["usermod", "-u", "12000", "-g", "alice_sftp"]
    assert ["usermod", "-p", "*", "alice"] in commands
    assert ["usermod", "-p", "!", "alice"] in commands


def test_ensure_directories_sets_chroot_and_upload_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "server": {"data_dir": str(tmp_path / "data")},
            "isolation": {"root_permissions": "755", "upload_permissions": "750"},
        }
    )
    resolved = ResolvedUser(spec=user("alice", uid=12000, gid=12000), uid=12000, gid=12000)
    chowns: list[tuple[str, int, int]] = []

    monkeypatch.setattr(
        runtime_module.os,
        "chown",
        lambda path, uid, gid: chowns.append((str(path), uid, gid)),
    )

    ensure_directories(config, resolved)

    root = tmp_path / "data" / "alice"
    upload = root / "upload"
    assert root.is_dir()
    assert upload.is_dir()
    assert (root.stat().st_mode & 0o777) == 0o755
    assert (upload.stat().st_mode & 0o777) == 0o750
    assert chowns == [(str(root), 0, 0), (str(upload), 12000, 12000)]


def test_disable_missing_marks_existing_system_users_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    state = RuntimeState(users={"alice": RuntimeUserState(uid=12000, gid=12000)})
    commands: list[list[str]] = []

    monkeypatch.setattr(runtime_module, "user_exists", lambda username: True)
    monkeypatch.setattr(runtime_module, "run_command", commands.append)

    disable_missing(config, ProviderUsers(users=[]), state)

    assert commands == [["usermod", "-p", "!", "alice"]]
    assert state.users["alice"].disabled


def test_disable_missing_returns_when_feature_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "sync": {"disable_missing_users": False},
        }
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(runtime_module, "run_command", calls.append)

    disable_missing(
        config,
        ProviderUsers(users=[]),
        RuntimeState(users={"alice": RuntimeUserState(uid=12000, gid=12000)}),
    )

    assert calls == []


def test_user_exists_handles_present_and_missing_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module.pwd, "getpwnam", lambda username: object())
    assert runtime_module.user_exists("alice")

    def missing_user(_username: str) -> object:
        raise KeyError

    monkeypatch.setattr(runtime_module.pwd, "getpwnam", missing_user)
    assert not runtime_module.user_exists("alice")


def test_write_authorized_keys_uses_restricted_key_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_path = runtime_module.Path
    auth_dir = tmp_path / "authorized_keys"
    chowns: list[tuple[str, int, int]] = []

    def fake_path(value: str) -> Path:
        if value == "/etc/sftpwarden/authorized_keys":
            return auth_dir
        return real_path(value)

    monkeypatch.setattr(runtime_module, "Path", fake_path)
    monkeypatch.setattr(
        runtime_module.os,
        "chown",
        lambda path, uid, gid: chowns.append((str(path), uid, gid)),
    )
    resolved = ResolvedUser(
        spec=user(
            "alice",
            public_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"],
        ),
        uid=12000,
        gid=12000,
    )

    write_authorized_keys(SFTPWardenConfig(project=ProjectConfig(name="dev")), resolved)

    target = auth_dir / "alice"
    assert target.read_text(encoding="utf-8").startswith("restrict ssh-ed25519")
    assert (target.stat().st_mode & 0o777) == 0o644
    assert chowns == [(str(target), 0, 0)]


def test_load_runtime_inputs_loads_config_users_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "server": {"state_dir": str(tmp_path / "state")},
            "provider": {"type": "yaml", "path": str(tmp_path / "users.yaml")},
        }
    )
    config_path = tmp_path / "sftpwarden.yaml"
    users_path = tmp_path / "users.yaml"
    write_config(config_path, config)
    users_path.write_text("users: []\n", encoding="utf-8")
    RuntimeState(users={"alice": RuntimeUserState(uid=10000, gid=10000)}).save(
        tmp_path / "state" / "state.json"
    )

    loaded, users, state = load_runtime_inputs(config_path)

    assert loaded.project.name == "dev"
    assert users.users == []
    assert state.users["alice"].uid == 10000


def test_apply_once_saves_fingerprint_when_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "server": {"state_dir": str(tmp_path / "state")},
        }
    )
    state = RuntimeState(users={})
    users = ProviderUsers(users=[])

    monkeypatch.setattr(runtime_module, "load_runtime_inputs", lambda _path: (config, users, state))
    monkeypatch.setattr(runtime_module, "render_sshd_config", lambda _config: None)

    result = apply_once("config.yaml")

    assert result == "No user changes detected."
    assert RuntimeState.load(tmp_path / "state" / "state.json").fingerprint == state.fingerprint


def test_apply_once_applies_changed_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "server": {"state_dir": str(tmp_path / "state")},
        }
    )
    users = ProviderUsers(users=[user("alice")])
    state = RuntimeState(users={})
    calls: list[str] = []

    monkeypatch.setattr(runtime_module, "load_runtime_inputs", lambda _path: (config, users, state))
    monkeypatch.setattr(runtime_module, "render_sshd_config", lambda _config: calls.append("sshd"))
    monkeypatch.setattr(runtime_module, "ensure_group", lambda _group: calls.append("group"))
    monkeypatch.setattr(runtime_module, "ensure_system_user", lambda *_args: calls.append("user"))
    monkeypatch.setattr(runtime_module, "ensure_directories", lambda *_args: calls.append("dirs"))
    monkeypatch.setattr(
        runtime_module, "write_authorized_keys", lambda *_args: calls.append("keys")
    )
    monkeypatch.setattr(runtime_module, "disable_missing", lambda *_args: calls.append("missing"))

    result = apply_once("config.yaml")

    assert result.startswith("Applied 1 action(s):")
    assert calls == ["sshd", "group", "user", "dirs", "keys", "missing"]
    assert RuntimeState.load(tmp_path / "state" / "state.json").users["alice"].uid == 10000


def test_run_sync_loop_prints_changes_until_sleep_stops(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = SFTPWardenConfig.model_validate(
        {"version": 1, "project": {"name": "dev"}, "sync": {"interval_seconds": 5}}
    )
    monkeypatch.setattr(runtime_module, "load_config", lambda _path: config)
    monkeypatch.setattr(runtime_module, "apply_once", lambda _path: "Applied 1 action(s).")
    monkeypatch.setattr(
        runtime_module.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(KeyboardInterrupt):
        run_sync_loop("config.yaml")

    assert "Applied 1 action(s)." in capsys.readouterr().out
