from __future__ import annotations

from pathlib import Path

import pytest

from sftpwarden.config import ProjectConfig, SFTPWardenConfig
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.providers import ProviderUsers, SFTPUser
from sftpwarden.runtime import (
    RuntimeState,
    RuntimeUserState,
    allocate_users,
    build_runtime_plan,
    parse_state_users,
    validate_runtime_users,
)

TEST_SHADOW_HASH = "$6$rounds=500000$saltstring$hashvalue"


def user(
    username: str,
    *,
    password_hash: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    public_keys: list[str] | None = None,
    disabled: bool = False,
) -> SFTPUser:
    return SFTPUser(
        username=username,
        password_hash=TEST_SHADOW_HASH if password_hash is None else password_hash,
        uid=uid,
        gid=gid,
        public_keys=public_keys or [],
        disabled=disabled,
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


def test_runtime_plan_creates_new_user() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))

    plan = build_runtime_plan(config, ProviderUsers(users=[user("alice")]), RuntimeState(users={}))

    assert plan.changed is True
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

    assert plan.changed is False
    assert plan.actions == []


def test_runtime_plan_force_updates_existing_user() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    users = ProviderUsers(users=[user("alice")])
    initial_plan = build_runtime_plan(config, users, RuntimeState(users={}))
    state = RuntimeState(
        users={"alice": RuntimeUserState(uid=10000, gid=10000)},
        fingerprint=initial_plan.fingerprint,
    )

    plan = build_runtime_plan(config, users, state, force=True)

    assert plan.changed is True
    assert plan.actions[0].action == "update"


def test_runtime_plan_disables_missing_user() -> None:
    config = SFTPWardenConfig(project=ProjectConfig(name="dev"))
    state = RuntimeState(users={"alice": RuntimeUserState(uid=10000, gid=10000)})

    plan = build_runtime_plan(config, ProviderUsers(users=[]), state)

    assert plan.changed is True
    assert plan.actions[0].action == "disable"
    assert plan.actions[0].reason == "missing from provider"
