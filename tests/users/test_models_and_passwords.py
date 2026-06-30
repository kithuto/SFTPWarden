from __future__ import annotations

import pytest
from pydantic import ValidationError

from sftpwarden.providers import (
    ProviderUsers,
)
from sftpwarden.security.passwords import hash_password, resolve_password_hash
from sftpwarden.users import SFTPUser
from sftpwarden.users.service import remove_user
from sftpwarden.utils.errors import ProviderError

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


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
