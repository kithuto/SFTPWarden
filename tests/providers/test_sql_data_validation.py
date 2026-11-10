from copy import deepcopy
from pathlib import Path
from traceback import format_exception
from typing import Any

import pytest

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import SQLiteProvider
from sftpwarden.providers.sql import parse_sql_bool, users_from_sql_rows
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.errors import ProviderError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFMsDFmvcbuEKKQ2nRx6M4jxyJuS9KoOesN7GyrXw+d+"
USER = {"username": "alice", "disabled": False}
KEY_ROW = {"username": "alice", "name": "main", "public_key": KEY}


@pytest.mark.parametrize("value", ["truue", "unknown", "", None, 2, -1, 0.5, [], {}, b"bad"])
def test_invalid_sql_booleans_are_not_interpreted_as_authorization(value: Any) -> None:
    with pytest.raises(ProviderError, match="SQL.*boolean"):
        parse_sql_bool(value)


@pytest.mark.parametrize("value", [False, 0, "false", " 0 ", "f", "no", "n", "off", b"\x00"])
def test_supported_false_sql_values(value: Any) -> None:
    assert parse_sql_bool(value) is False


@pytest.mark.parametrize("value", [True, 1, "true", " 1 ", "t", "yes", "y", "on", b"\x01"])
def test_supported_true_sql_values(value: Any) -> None:
    assert parse_sql_bool(value) is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("metadata", "not-json"),
        ("metadata", "[]"),
        ("metadata", "false"),
        ("metadata", "null"),
        ("metadata", ""),
        ("metadata", 0),
        ("metadata", []),
        ("metadata", '{"owner": "first", "owner": "second"}'),
        ("metadata", '{"nested": {"owner": 1, "owner": 2}}'),
        ("metadata", '{"n": NaN}'),
        ("metadata", '{"n": Infinity}'),
        ("metadata", '{"n": -Infinity}'),
        ("disabled", "truue"),
        ("username", None),
        ("username", "BadName"),
        ("expires_at", "private-invalid-value"),
        ("expires_at", 0),
        ("expires_at", []),
        ("fingerprint", 0),
        ("source", False),
    ],
)
def test_invalid_sql_key_rows_fail_without_silently_losing_metadata(field: str, value: Any) -> None:
    row = {**KEY_ROW, field: value}
    with pytest.raises(ProviderError, match="Invalid SQL provider") as error:
        users_from_sql_rows([USER], key_rows=[row], schema_version=2)
    assert "private-invalid-value" not in str(error.value)
    assert "private-invalid-value" not in "".join(format_exception(error.value))


@pytest.mark.parametrize("field", ["username", "name", "public_key"])
def test_incomplete_sql_key_rows_are_not_skipped(field: str) -> None:
    row = dict(KEY_ROW)
    del row[field]
    with pytest.raises(ProviderError, match="Invalid SQL provider"):
        users_from_sql_rows([USER], key_rows=[row], schema_version=2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", 12000.5),
        ("gid", 12000.5),
        ("public_keys", {KEY: True}),
        ("disabled", "truue"),
        ("password_hash", "private-invalid-value"),
        ("password_hash", False),
        ("upload_dir", 0),
    ],
)
def test_invalid_sql_users_are_rejected_without_echoing_values(field: str, value: Any) -> None:
    with pytest.raises(ProviderError, match="Invalid SQL provider") as error:
        users_from_sql_rows([{**USER, field: value}])
    assert "private-invalid-value" not in str(error.value)
    assert "private-invalid-value" not in "".join(format_exception(error.value))


def test_missing_key_result_is_not_an_empty_key_set() -> None:
    with pytest.raises(ProviderError, match="Invalid SQL provider"):
        users_from_sql_rows([USER], schema_version=2)
    explicit_empty = users_from_sql_rows([USER], key_rows=[], schema_version=2)
    assert explicit_empty.users[0].keys == []


def test_nullable_metadata_and_filtered_queries_remain_supported() -> None:
    rows = [dict(USER)]
    # A custom query may intentionally select only Alice, while the key table has Bob.
    keys = [
        dict(KEY_ROW, metadata=None),
        dict(KEY_ROW, username="bob", metadata={"nested": {"n": 1}}),
    ]
    original = deepcopy((rows, keys))
    users = users_from_sql_rows(rows, key_rows=keys, schema_version=2)
    assert [user.username for user in users.users] == ["alice"]
    assert users.users[0].keys[0].metadata == {}
    assert (rows, keys) == original


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_invalid_metadata_write_rolls_back_user_and_key_changes(
    tmp_path: Path, value: float
) -> None:
    provider = SQLiteProvider(
        ProviderConfig(type=ProviderType.SQLITE, user_schema=2), path=tmp_path / "users.sqlite"
    )
    original = ProviderUsers(
        schema_version=2,
        users=[
            SFTPUser(
                username="alice",
                disabled=True,
                keys=[SFTPUserKey(name="main", public_key=KEY)],
            )
        ],
    )
    provider.write(original)
    replacement = original.model_copy(deep=True)
    replacement.users[0].disabled = False
    replacement.users[0].keys[0].metadata["invalid"] = value
    with pytest.raises(ValueError, match="JSON"):
        provider.write(replacement)
    assert provider.read() == original
