import runpy

import pytest
import yaml

from sftpwarden.config import load_config
from sftpwarden.providers import documents, provider_from_config
from sftpwarden.services.provider_transfer import (
    deserialize_users,
    import_provider_users,
    serialize_users,
)
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


@pytest.mark.parametrize(
    ("fmt", "document"),
    [
        ("yaml", ""),
        ("yaml", "users: null"),
        ("yaml", "{}"),
        ("yaml", "users: []\nusers: []\n"),
        ("yaml", "users: [invalid"),
        ("json", ""),
        ("json", "{}"),
        ("json", '{"users": null}'),
        ("json", '{"users": [], "users": []}'),
        ("json", '{"users": ['),
        ("csv", ""),
        ("csv", "usernam\n"),
        ("csv", "username,username\n"),
        ("csv", "username,password_hash\nalice\n"),
        ("csv", 'username,comment\nalice,"unfinished\n'),
    ],
)
def test_incomplete_transfer_document_is_rejected(fmt, document):
    with pytest.raises(ProviderError):
        deserialize_users(document, fmt)


@pytest.mark.parametrize("fmt", ["yaml", "json", "csv"])
@pytest.mark.parametrize("schema", [1, 2])
def test_explicit_empty_transfer_is_preserved(fmt, schema):
    users = ProviderUsers(schema_version=schema, users=[])
    assert deserialize_users(serialize_users(users, fmt), fmt) == users


@pytest.mark.parametrize("fmt", ["yaml", "json", "csv"])
def test_incomplete_import_preserves_existing_provider(local_project_factory, fmt):
    root, entry = local_project_factory()
    config = load_config(entry.config)
    provider = provider_from_config(root, config)
    before = ProviderUsers(users=[SFTPUser(username="alice", comment="preserve")])
    provider.write(before)
    source = root / f"incomplete.{fmt}"
    source.write_text("", encoding="utf-8")
    with pytest.raises(ProviderError):
        import_provider_users(
            context_name=entry.name,
            input_path=str(source),
            mode="replace",
            fmt=fmt,
            no_refresh=True,
        )
    assert provider.read() == before


def test_yaml_merge_defaults_remain_supported():
    document = """
defaults: &defaults
  disabled: true
  comment: inherited
users:
  - <<: *defaults
    username: alice
    disabled: false
"""
    user = deserialize_users(document, "yaml").users[0]
    assert not user.disabled
    assert user.comment == "inherited"


@pytest.mark.parametrize("pure_python", [False, True])
def test_safe_yaml_engines_preserve_merges_and_reject_duplicate_keys(monkeypatch, pure_python):
    if pure_python:
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
        parser = runpy.run_path(documents.__file__)["parse_provider_document"]
    else:
        parser = documents.parse_provider_document
    users = parser(
        "defaults: &defaults {disabled: true}\n"
        "users: [{<<: *defaults, username: alice, disabled: false}]\n",
        "yaml",
    )
    assert not users.users[0].disabled
    for invalid in (
        "users: [{username: alice, disabled: true, disabled: false}]",
        "users: !!python/object/apply:os.system ['echo unsafe']",
        "users: [{username: alice",
    ):
        with pytest.raises(ProviderError):
            parser(invalid, "yaml")


@pytest.mark.parametrize("fmt", ["yaml", "json", "csv"])
def test_transfer_errors_do_not_reveal_invalid_credentials(fmt):
    secret = "synthetic-invalid-private-credential"
    text = {
        "yaml": f"users:\n- username: alice\n  password_hash: {secret}\n",
        "json": '{"users":[{"username":"alice","password_hash":"' + secret + '"}]}',
        "csv": f"username,password_hash\nalice,{secret}\n",
    }[fmt]
    with pytest.raises(ProviderError) as failure:
        deserialize_users(text, fmt)
    assert secret not in str(failure.value)
