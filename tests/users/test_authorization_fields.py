import csv
import io
import json
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import CSVProvider
from sftpwarden.providers.documents import parse_provider_document
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.users.schemas import user_schema, users_from_mapping
from sftpwarden.utils.errors import ProviderError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFMsDFmvcbuEKKQ2nRx6M4jxyJuS9KoOesN7GyrXw+d+"


def csv_document(schema: int, disabled: str, *, keys: str | None = None) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["username", "public_keys" if schema == 1 else "keys", "disabled"])
    value = KEY if schema == 1 else keys or json.dumps([{"name": "main", "public_key": KEY}])
    writer.writerow(["alice", value, disabled])
    return output.getvalue()


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("value", ["truue", "2", "private-invalid-value"])
def test_unknown_csv_boolean_fails_provider_validation(schema: int, value: str) -> None:
    with pytest.raises(ProviderError, match="Invalid CSV provider") as error:
        parse_provider_document(csv_document(schema, value), "csv")
    assert "private-invalid-value" not in str(error.value)


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize(
    "value,expected",
    [
        (" true ", True),
        ("YES", True),
        ("on", True),
        ("t", True),
        ("1", True),
        (" false ", False),
        ("0", False),
        ("OFF", False),
        ("", False),
        ("  ", False),
    ],
)
def test_csv_booleans_have_explicit_portable_semantics(
    schema: int, value: str, expected: bool
) -> None:
    users = parse_provider_document(csv_document(schema, value), "csv")
    assert users.users[0].disabled is expected


@pytest.mark.parametrize(
    "fields",
    [
        '"disabled":true,"disabled":false',
        '"expires_at":"2000-01-01","expires_at":null',
        '"metadata":{"owner":1,"owner":2}',
        '"metadata":{"n":NaN}',
    ],
)
def test_csv_named_key_json_cannot_override_fields_or_use_non_json_numbers(fields: str) -> None:
    key = '[{"name":"main","public_key":' + json.dumps(KEY) + "," + fields + "}]"
    with pytest.raises(ProviderError, match="Invalid CSV provider"):
        parse_provider_document(csv_document(2, "false", keys=key), "csv")


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("value", [None, False, 0, "", {}])
@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_malformed_key_containers_are_not_an_empty_policy(
    schema: int, value: object, fmt: str
) -> None:
    document = {"schema_version": schema, "users": [{"username": "alice", "keys": value}]}
    text = yaml.safe_dump(document) if fmt == "yaml" else json.dumps(document)
    with pytest.raises(ProviderError, match="Invalid .* provider"):
        parse_provider_document(text, fmt)


def test_schema_v2_explicit_empty_named_keys_override_legacy_public_keys() -> None:
    users = users_from_mapping(
        {
            "schema_version": 2,
            "users": [
                {"username": "alice", "keys": [], "public_keys": [KEY]},
            ],
        }
    )
    assert users.users[0].key_objects() == []
    assert users.users[0].public_keys == []


def test_omitted_named_keys_preserve_legacy_conversion_and_explicit_empty_v1() -> None:
    for schema in (1, 2):
        user = users_from_mapping(
            {
                "schema_version": schema,
                "users": [
                    {"username": "alice", "public_keys": [KEY]},
                ],
            }
        ).users[0]
        assert user.active_public_keys() == [KEY]
        assert user_schema(schema).user_from_csv_row({"username": "alice"}).disabled is False
    user = users_from_mapping(
        {
            "schema_version": 1,
            "users": [
                {"username": "alice", "keys": [], "public_keys": [KEY]},
            ],
        }
    ).users[0]
    assert user.active_public_keys() == [KEY]


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("disabled", [False, True])
def test_csv_schema_serializer_round_trip_preserves_boolean(schema: int, disabled: bool) -> None:
    adapter = user_schema(schema)
    user = SFTPUser(username="alice", public_keys=[KEY], disabled=disabled)
    assert adapter.user_from_csv_row(adapter.csv_row_from_user(user)).disabled is disabled


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_csv_write_does_not_publish_non_json_metadata(tmp_path: Path, value: float) -> None:
    path = tmp_path / "users.csv"
    provider = CSVProvider(ProviderConfig(type=ProviderType.CSV, user_schema=2), path=path)
    original = ProviderUsers(
        users=[
            SFTPUser(
                username="alice", disabled=True, keys=[SFTPUserKey(name="main", public_key=KEY)]
            )
        ]
    )
    provider.write(original)
    before = path.read_bytes()
    replacement = original.model_copy(deep=True)
    replacement.users[0].disabled = False
    replacement.users[0].keys[0].metadata["invalid"] = value
    with pytest.raises(ValueError, match="JSON"):
        provider.write(replacement)
    assert path.read_bytes() == before
    assert provider.read() == original
