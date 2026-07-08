from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from sftpwarden.providers import (
    ProviderUsers,
)
from sftpwarden.security.passwords import hash_password, resolve_password_hash
from sftpwarden.users import SFTPUser
from sftpwarden.users.models import SFTPUserKey, parse_datetime, public_key_fingerprint
from sftpwarden.users.service import remove_user
from sftpwarden.utils.errors import ProviderError

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"
TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"
SECOND_TEST_KEY = "ssh-ed25519 ZmFrZS11c2VyLW1vZGVsLTI="


def test_password_helpers_validate_inputs() -> None:
    with pytest.raises(ProviderError, match="empty"):
        hash_password("")
    with pytest.raises(ProviderError, match="at least 8"):
        hash_password("short")
    with pytest.raises(ProviderError, match="whitespace"):
        resolve_password_hash(password=None, password_hash=f" {TEST_HASH}")
    assert resolve_password_hash(password=None, password_hash=TEST_HASH) == TEST_HASH


def test_user_model_validation_edges() -> None:
    with pytest.raises(ValidationError, match="requires at least one"):
        SFTPUser(username="empty")
    with pytest.raises(ValidationError, match="Username"):
        SFTPUser(username="BadUser", password_hash=TEST_HASH)
    with pytest.raises(ValidationError, match="SSH public key"):
        SFTPUser(username="alice", public_keys=["not-a-key"])
    with pytest.raises(ValidationError, match="SSH public key"):
        SFTPUser(username="alice", public_keys=["ssh-ed25519"])
    with pytest.raises(ValidationError, match="SSH public key"):
        SFTPUser(username="alice", public_keys=["ssh-ed25519 !!!!"])
    with pytest.raises(ValidationError, match="plaintext"):
        SFTPUser(username="alice", password_hash="plaintext")  # noqa: S106
    with pytest.raises(ValidationError, match="duplicate usernames"):
        ProviderUsers(
            users=[
                SFTPUser(username="alice", password_hash=TEST_HASH),
                SFTPUser(username="alice", password_hash=TEST_HASH),
            ]
        )
    with pytest.raises(ValidationError, match="duplicate explicit UIDs"):
        ProviderUsers(
            users=[
                SFTPUser(username="alice", uid=12000, password_hash=TEST_HASH),
                SFTPUser(username="bob", uid=12000, password_hash=TEST_HASH),
            ]
        )
    with pytest.raises(ProviderError, match="Unknown user"):
        remove_user(ProviderUsers(users=[]), "missing")


def test_named_key_model_derives_fingerprint_and_validates_duplicates() -> None:
    key = SFTPUserKey(name="prod-ci", public_key=TEST_KEY, expires_at="2999-01-01")
    disabled = SFTPUserKey(name="disabled", public_key=SECOND_TEST_KEY, disabled=True)
    expired = SFTPUserKey(name="expired", public_key=SECOND_TEST_KEY, expires_at="2000-01-01")
    existing_key_user = SFTPUser(username="carol", keys=[key])

    assert key.fingerprint == public_key_fingerprint(TEST_KEY)
    assert key.is_active()
    assert not disabled.is_active()
    assert not expired.is_active(now=datetime(2026, 1, 1))
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime(date(2027, 1, 1)) == datetime(2027, 1, 1, tzinfo=UTC)
    assert existing_key_user.key_objects() == [key]

    with pytest.raises(ValidationError, match="Key name"):
        SFTPUserKey(name="BadName", public_key=TEST_KEY)
    with pytest.raises(ValidationError, match="fingerprint does not match"):
        SFTPUserKey(name="bad-fingerprint", public_key=TEST_KEY, fingerprint="SHA256:wrong")
    with pytest.raises(ValueError, match="datetime value"):
        parse_datetime(object())
    with pytest.raises(ValidationError, match="duplicate key names"):
        SFTPUser(
            username="alice",
            keys=[
                SFTPUserKey(name="prod-ci", public_key=TEST_KEY),
                SFTPUserKey(name="prod-ci", public_key=TEST_KEY.replace("Tests", "Test2")),
            ],
        )
    with pytest.raises(ValidationError, match="duplicate key fingerprints"):
        SFTPUser(
            username="alice",
            keys=[
                SFTPUserKey(name="prod-a", public_key=TEST_KEY),
                SFTPUserKey(name="prod-b", public_key=TEST_KEY),
            ],
        )
