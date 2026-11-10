"""New accounts stay locked until group and filesystem isolation are ready."""

import pytest

import sftpwarden.runtime.core as runtime
from sftpwarden.config import default_project_config
from sftpwarden.users import ProviderUsers, SFTPUser


def test_batch_appends_members_before_granting_and_bounds_command_size(monkeypatch):
    config = default_project_config("batch")
    users = [
        runtime.ResolvedUser(SFTPUser(username=f"user{i}", disabled=i == 0), 10000 + i, 10000 + i)
        for i in range(257)
    ]
    commands = []
    monkeypatch.setattr(runtime, "ensure_group", lambda *_: None)
    verified = []
    monkeypatch.setattr(runtime, "verify_locked_accounts", verified.append)
    monkeypatch.setattr(runtime, "run_command", commands.append)
    grants = []

    def apply(args, **kwargs):
        if args[0] == "newusers":
            assert not verified and not commands
        else:
            assert verified == [[f"user{i}" for i in range(257)]]
            assert len(commands) == 2
        grants.append((args, kwargs))

    monkeypatch.setattr(runtime, "run_checked", apply)
    runtime.create_locked_accounts(config, users)
    assert [len(command[3].split(",")) for command in commands] == [256, 1]
    assert all(command[:3] == ["groupmod", "--append", "--users"] for command in commands)
    assert grants[0][0] == ["newusers"]
    records = [line.split(":") for line in grants[0][1]["input_text"].splitlines()]
    assert len(records) == 257
    for index, record in enumerate(records):
        assert record[:6] == [
            f"user{index}",
            "!",
            str(10000 + index),
            f"user{index}_sftp",
            "",
            f"{config.server.data_dir}/user{index}",
        ]
        assert record[6].endswith("/nologin")
    assert grants[1][0] == ["chpasswd", "--encrypted"]
    assert grants[1][1]["input_text"].splitlines() == [
        "user0:!",
        *[f"user{i}:*" for i in range(1, 257)],
    ]
    assert all(call[1]["private_output"] is True for call in grants)


@pytest.mark.parametrize("separator", [":", "\r", "\n", "\0"])
@pytest.mark.parametrize("field", ["password", "home"])
def test_batch_rejects_record_injection_before_commands(monkeypatch, separator, field):
    config = default_project_config("batch")
    config.auth.allow_password = True
    user = SFTPUser(username="alice")
    if field == "password":
        user.password_hash = f"$6$hash{separator}root:injected"
    else:
        config.server.data_dir = f"/data{separator}injected"
    monkeypatch.setattr(runtime, "ensure_group", lambda *_: pytest.fail("mutated groups"))
    monkeypatch.setattr(runtime, "run_checked", lambda *_a, **_k: pytest.fail("mutated accounts"))
    monkeypatch.setattr(runtime, "run_command", lambda _: pytest.fail("mutated accounts"))
    with pytest.raises(runtime.RuntimeError, match="record separator"):
        runtime.create_locked_accounts(config, [runtime.ResolvedUser(user, 10000, 10000)])


@pytest.mark.parametrize("failure", ["newusers", "groupmod", "chpasswd"])
def test_partial_batch_preserves_pending_identity_and_recovers(tmp_path, monkeypatch, failure):
    config = default_project_config("batch")
    config.server.state_dir = str(tmp_path / "state")
    # Account records describe Linux container paths, even on a Windows test host.
    # Only the persisted state below belongs in the host's temporary directory.
    config.server.data_dir = "/data"
    users = ProviderUsers(users=[SFTPUser(username=name) for name in ("alice", "bob")])
    existing = set()
    prepared = set()
    before = runtime.RuntimeState(users={}, fingerprint="previous")
    before.save(runtime.state_path(config))
    monkeypatch.setattr(
        runtime,
        "load_runtime_inputs",
        lambda _: (config, users, runtime.RuntimeState.load(runtime.state_path(config))),
    )
    monkeypatch.setattr(runtime, "validate_system_accounts", lambda *_: None)
    monkeypatch.setattr(runtime, "validate_retained_data", lambda *_: None)
    monkeypatch.setattr(runtime, "ensure_group", lambda *_: None)
    monkeypatch.setattr(runtime, "verify_locked_accounts", lambda _: None)
    monkeypatch.setattr(runtime, "user_exists", existing.__contains__)
    monkeypatch.setattr(runtime, "write_authorized_keys", lambda *_: None)
    monkeypatch.setattr(
        runtime, "ensure_directories", lambda _, user: prepared.add(user.spec.username)
    )

    def command(args):
        assert prepared == {"alice", "bob"}
        if args[0] == failure:
            raise OSError("injected account failure")

    def grant(args, **kwargs):
        assert prepared == {"alice", "bob"}
        if args[0] == "newusers":
            existing.add("alice")
        if args[0] == failure:
            raise OSError("injected account failure")
        if args[0] == "newusers":
            existing.add("bob")

    monkeypatch.setattr(runtime, "run_command", command)
    monkeypatch.setattr(runtime, "run_checked", grant)
    with pytest.raises(OSError, match="injected"):
        runtime._apply_once("config.yaml", force=False)
    pending = runtime.RuntimeState.load(runtime.state_path(config))
    assert pending.pending and pending.fingerprint == "previous"
    assert set(pending.users) == {"alice", "bob"}
    assert all(user.applied_fingerprint is None for user in pending.users.values())
    monkeypatch.setattr(runtime, "run_command", lambda _: None)
    monkeypatch.setattr(runtime, "run_checked", lambda *_args, **_kwargs: None)
    runtime._apply_once("config.yaml", force=False)
    repaired = runtime.RuntimeState.load(runtime.state_path(config))
    assert not repaired.pending and repaired.fingerprint != "previous"
    assert repaired.uid_map == pending.uid_map
    assert all(user.applied_fingerprint for user in repaired.users.values())


@pytest.mark.parametrize(
    "shadow",
    [
        "alice:!:::::::\nbob:!:::::::\n",
        "alice:*:::::::\nbob:!:::::::\n",
        "alice:!:::::::\nbadline\n",
    ],
)
def test_batch_requires_every_new_account_to_be_locked(monkeypatch, shadow):
    monkeypatch.setattr(runtime.Path, "read_text", lambda *_args, **_kwargs: shadow)
    if "alice:!" in shadow and "bob:!" in shadow:
        runtime.verify_locked_accounts(["alice", "bob"])
    else:
        with pytest.raises(runtime.RuntimeError, match="not created locked"):
            runtime.verify_locked_accounts(["alice", "bob"])


def test_batch_does_not_grant_membership_after_unexpected_password_policy(monkeypatch):
    config = default_project_config("batch")
    users = [runtime.ResolvedUser(SFTPUser(username="alice"), 10000, 10000)]
    monkeypatch.setattr(runtime, "ensure_group", lambda *_: None)
    monkeypatch.setattr(runtime.Path, "read_text", lambda *_a, **_k: "alice:unexpected:::::::\n")
    monkeypatch.setattr(runtime, "run_command", lambda _: pytest.fail("granted membership"))
    commands = []
    monkeypatch.setattr(runtime, "run_checked", lambda args, **_: commands.append(args))
    with pytest.raises(runtime.RuntimeError, match="not created locked"):
        runtime.create_locked_accounts(config, users)
    assert commands == [["newusers"]]
