import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import sftpwarden.runtime.core as runtime
from sftpwarden.config import default_project_config
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import RuntimeError


def test_t07_removed_user_keeps_its_uid_and_gid_reserved() -> None:
    state = runtime.RuntimeState({"retired": runtime.RuntimeUserState(10000, 10001)})
    users = ProviderUsers(users=[SFTPUser(username="new_user")])
    resolved = runtime.allocate_users(default_project_config("ids"), users, state)
    assert resolved[0].uid not in {10000, 10001}
    assert resolved[0].gid not in {10000, 10001}


@pytest.mark.parametrize("field", ["uid", "gid"])
def test_t07_explicit_id_cannot_claim_retained_data(field: str) -> None:
    state = runtime.RuntimeState({"retired": runtime.RuntimeUserState(12000, 12000)})
    users = ProviderUsers(users=[SFTPUser(username="new_user", **{field: 12000})])
    with pytest.raises(RuntimeError, match="reserved"):
        runtime.allocate_users(default_project_config("ids"), users, state)


def test_t07_existing_group_must_match_requested_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime, "grp", SimpleNamespace(getgrnam=lambda _: SimpleNamespace(gr_gid=9999))
    )
    with pytest.raises(RuntimeError, match="GID"):
        runtime.ensure_group("alice_sftp", 12000)


def test_t07_unmanaged_account_cannot_be_adopted(monkeypatch: pytest.MonkeyPatch) -> None:
    alice = runtime.ResolvedUser(SFTPUser(username="alice"), 12000, 12000)
    monkeypatch.setattr(runtime, "grp", SimpleNamespace())
    monkeypatch.setattr(
        runtime,
        "pwd",
        SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=12000, pw_gid=12000)),
    )
    with pytest.raises(RuntimeError, match="managed"):
        runtime.validate_system_accounts([alice], runtime.RuntimeState({}))


def test_t07_retained_identity_cannot_be_renumbered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "pwd", SimpleNamespace())
    monkeypatch.setattr(runtime, "grp", SimpleNamespace())
    alice = runtime.ResolvedUser(SFTPUser(username="alice"), 12001, 12001)
    with pytest.raises(RuntimeError, match="Cannot change retained UID/GID"):
        runtime.validate_system_accounts(
            [alice], runtime.RuntimeState({"alice": runtime.RuntimeUserState(12000, 12000)})
        )


def test_t07_corrupt_duplicate_state_is_not_used(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"uid_map": {"alice": 10000, "bob": 10000}}))
    with pytest.raises(RuntimeError, match="duplicate UID"):
        runtime.RuntimeState.load(path)
