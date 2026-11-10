from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import sftpwarden.runtime.core as runtime
from sftpwarden.config import ProjectConfig, SFTPWardenConfig
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.users.schemas.v2 import UserSchemaV2

# Public-only RFC 8032 test-vector key; no private material is needed here.
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea"


def config() -> SFTPWardenConfig:
    return SFTPWardenConfig(project=ProjectConfig(name="policy"))


def test_t01_last_key_deletion_is_a_representable_deny_policy() -> None:
    alice = SFTPUser(username="alice", keys=[SFTPUserKey(name="only", public_key=KEY)])
    revoked = UserSchemaV2().remove_key(alice, alice.keys[0])
    assert revoked.key_objects() == []
    assert not revoked.disabled
    plan = runtime.build_runtime_plan(
        config(), ProviderUsers(users=[revoked]), runtime.RuntimeState({})
    )
    assert plan.changed


@pytest.mark.parametrize("password_hash", [None, "!", "*"])
def test_t01_inactive_last_key_does_not_abort_reconciliation(password_hash: str | None) -> None:
    alice = SFTPUser(
        username="alice",
        password_hash=password_hash,
        keys=[SFTPUserKey(name="only", public_key=KEY, disabled=True)],
    )
    plan = runtime.build_runtime_plan(
        config(), ProviderUsers(users=[alice]), runtime.RuntimeState({})
    )
    assert plan.changed


def test_t02_expiry_changes_plan_without_provider_edit() -> None:
    boundary = datetime(2030, 1, 1, tzinfo=UTC)
    users = ProviderUsers(
        users=[
            SFTPUser(
                username="alice",
                keys=[SFTPUserKey(name="expires", public_key=KEY, expires_at=boundary)],
            )
        ]
    )
    initial = runtime.build_runtime_plan(
        config(), users, runtime.RuntimeState({}), now=boundary - timedelta(seconds=1)
    )
    state = runtime.RuntimeState(
        {"alice": runtime.RuntimeUserState(10000, 10000)}, initial.fingerprint
    )
    assert not runtime.build_runtime_plan(
        config(), users, state, now=boundary - timedelta(microseconds=1)
    ).changed
    assert runtime.build_runtime_plan(config(), users, state, now=boundary).changed
    assert runtime.build_runtime_plan(
        config(), users, state, now=boundary + timedelta(seconds=1)
    ).changed


def test_t02_authorized_key_contains_utc_deadline() -> None:
    key = SFTPUserKey(name="expires", public_key=KEY, expires_at="2030-01-01T09:00:00+09:00")
    assert runtime.authorized_key_line("restrict", key).startswith(
        'restrict,expiry-time="20291231235959Z" '
    )


@pytest.mark.parametrize("disabled,allow_keys", [(True, True), (False, False)])
def test_t01_disabled_policy_removes_installed_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled: bool,
    allow_keys: bool,
) -> None:
    real_path = Path
    monkeypatch.setattr(
        runtime,
        "Path",
        lambda value: tmp_path if value == "/etc/sftpwarden/authorized_keys" else real_path(value),
    )
    monkeypatch.setattr(runtime, "chown_path", lambda *args: None)
    (tmp_path / "alice").write_text(KEY)
    settings = config()
    settings.auth.allow_public_key = allow_keys
    alice = SFTPUser(username="alice", public_keys=[KEY], disabled=disabled)
    runtime.write_authorized_keys(settings, runtime.ResolvedUser(alice, 10000, 10000))
    assert (tmp_path / "alice").read_text() == ""


def test_t01_failed_key_publication_preserves_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_path = Path
    monkeypatch.setattr(
        runtime,
        "Path",
        lambda value: tmp_path if value == "/etc/sftpwarden/authorized_keys" else real_path(value),
    )
    monkeypatch.setattr(runtime, "chown_path", lambda *args: None)
    destination = tmp_path / "alice"
    destination.write_text(KEY)

    def fail_replace(*args: object) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failure"):
        runtime.write_authorized_keys(
            config(), runtime.ResolvedUser(SFTPUser(username="alice"), 10000, 10000)
        )
    assert destination.read_text() == KEY
    assert list(tmp_path.iterdir()) == [destination]


def test_t01_failed_publication_does_not_advance_applied_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reconciliation_lock,
) -> None:
    # Policy failure handling must not depend on the host's POSIX account database.
    monkeypatch.setattr(runtime, "pwd", None)
    monkeypatch.setattr(runtime, "user_exists", lambda _: True)
    settings = config()
    settings.server.state_dir = str(tmp_path)
    state = runtime.RuntimeState({"alice": runtime.RuntimeUserState(10000, 10000)}, "previous")
    state.save(tmp_path / "state.json")
    users = ProviderUsers(users=[SFTPUser(username="alice")])
    monkeypatch.setattr(runtime, "load_runtime_inputs", lambda _: (settings, users, state))
    monkeypatch.setattr(runtime, "load_config", lambda _: settings)
    monkeypatch.setattr(runtime, "render_sshd_config", lambda _: None)
    monkeypatch.setattr(runtime, "validate_system_accounts", lambda *_: None)
    monkeypatch.setattr(runtime, "ensure_group", lambda _: None)

    def fail_publish(*args: object) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(runtime, "write_authorized_keys", fail_publish)
    with pytest.raises(OSError, match="publication failure"):
        runtime.apply_once("config.yaml")
    assert runtime.RuntimeState.load(tmp_path / "state.json").fingerprint == "previous"
