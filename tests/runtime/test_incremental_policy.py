from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import sftpwarden.runtime.core as runtime
from sftpwarden.config import ProjectConfig, SFTPWardenConfig
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey, users_fingerprint

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea"


def test_inventory_fingerprint_preserves_the_existing_wire_contract():
    user = SFTPUser(
        username="alice",
        uid=2001,
        gid=3001,
        keys=[SFTPUserKey(name="office", public_key=KEY)],
        comment="Metadata",
    )
    assert users_fingerprint(ProviderUsers(schema_version=2, users=[user])) == (
        "d326c8830984ad473f5e2f79579756b1fdeaea691f56244509c7b9a20b071a44"
    )


def test_per_user_checkpoint_ignores_metadata_but_tracks_identity_and_policy():
    user = SFTPUser(username="alice", keys=[SFTPUserKey(name="office", public_key=KEY)])
    original = runtime.applied_user_fingerprint(runtime.ResolvedUser(user, 2001, 3001))
    user.comment = "Metadata only"
    assert runtime.applied_user_fingerprint(runtime.ResolvedUser(user, 2001, 3001)) == original
    assert runtime.applied_user_fingerprint(runtime.ResolvedUser(user, 2002, 3001)) != original
    assert runtime.applied_user_fingerprint(runtime.ResolvedUser(user, 2001, 3002)) != original
    user.keys[0].disabled = True
    assert runtime.applied_user_fingerprint(runtime.ResolvedUser(user, 2001, 3001)) != original


@pytest.fixture
def reconciliation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reconciliation_lock):
    config = SFTPWardenConfig(project=ProjectConfig(name="incremental"))
    config.server.state_dir = str(tmp_path / "state")
    config.server.data_dir = str(tmp_path / "data")
    users = ProviderUsers(
        users=[SFTPUser(username=name, public_keys=[KEY]) for name in ("alice", "bob")]
    )
    effects: list[tuple[str, str]] = []
    installed: dict[str, list[SFTPUserKey]] = {}

    def publish(_config, resolved):
        effects.append(("keys", resolved.spec.username))
        installed[resolved.spec.username] = resolved.spec.key_objects()

    monkeypatch.setattr(runtime, "load_config", lambda _: config)
    monkeypatch.setattr(
        runtime,
        "load_runtime_inputs",
        lambda _: (config, users, runtime.RuntimeState.load(runtime.state_path(config))),
    )
    monkeypatch.setattr(runtime, "validate_system_accounts", lambda *_: None)
    monkeypatch.setattr(runtime, "ensure_group", lambda *_: None)
    monkeypatch.setattr(runtime, "user_exists", lambda *_: True)
    monkeypatch.setattr(runtime, "run_command", lambda args: effects.append(("lock", args[-1])))
    monkeypatch.setattr(runtime, "write_authorized_keys", publish)
    monkeypatch.setattr(
        runtime,
        "ensure_system_user",
        lambda _, user: effects.append(("account", user.spec.username)),
    )
    monkeypatch.setattr(
        runtime, "ensure_directories", lambda _, user: effects.append(("dirs", user.spec.username))
    )
    runtime.apply_once("config.yaml")
    effects.clear()
    return config, users, effects, installed


def test_unrelated_user_is_not_reapplied_and_force_repairs_all(reconciliation) -> None:
    _, users, effects, _ = reconciliation
    users.users[0].public_keys = []
    runtime.apply_once("config.yaml")
    assert {username for _, username in effects} == {"alice"}
    effects.clear()
    runtime.apply_once("config.yaml", force=True)
    assert {username for _, username in effects} == {"alice", "bob"}


def test_reverted_provider_repairs_partial_effects_after_failure(
    reconciliation, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, users, effects, installed = reconciliation
    before = runtime.RuntimeState.load(runtime.state_path(config)).fingerprint
    original = users.model_copy(deep=True)
    users.users[0].public_keys = []
    users.users[1].upload_dir = "incoming"
    ensure_user = runtime.ensure_system_user

    def fail_second(_config, resolved):
        if resolved.spec.username == "bob":
            raise OSError("injected interruption after first user")
        ensure_user(_config, resolved)

    monkeypatch.setattr(runtime, "ensure_system_user", fail_second)
    with pytest.raises(OSError, match="injected interruption"):
        runtime.apply_once("config.yaml")
    assert not installed["alice"]
    assert runtime.RuntimeState.load(runtime.state_path(config)).fingerprint == before
    users.users = original.users
    monkeypatch.setattr(runtime, "ensure_system_user", ensure_user)
    effects.clear()
    runtime.apply_once("config.yaml")
    assert installed["alice"], "A reverted provider must repair the partially revoked account"
    effects.clear()
    assert runtime.apply_once("config.yaml") == "No user changes detected."
    assert not effects


def test_missing_user_is_revoked_once_and_recreated_if_returned(reconciliation) -> None:
    _, users, effects, installed = reconciliation
    removed = users.users.pop(0)
    runtime.apply_once("config.yaml")
    assert not installed["alice"]
    assert {username for _, username in effects} == {"alice"}
    effects.clear()
    assert runtime.apply_once("config.yaml") == "No user changes detected."
    assert not effects
    users.users.append(removed)
    runtime.apply_once("config.yaml")
    assert installed["alice"]
    assert {username for _, username in effects} == {"alice"}


def test_expiry_only_reapplies_affected_user(
    reconciliation, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, users, effects, installed = reconciliation
    boundary = datetime(2030, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        instant = boundary - timedelta(seconds=2)

        @classmethod
        def now(cls, tz=None):
            return cls.instant

    monkeypatch.setattr(runtime, "datetime", Clock)
    users.users[0].public_keys = []
    users.users[0].keys = [SFTPUserKey(name="expiry", public_key=KEY, expires_at=boundary)]
    runtime.apply_once("config.yaml")
    effects.clear()
    Clock.instant = boundary
    runtime.apply_once("config.yaml")
    assert not installed["alice"]
    assert installed["bob"]
    assert {username for _, username in effects} == {"alice"}
