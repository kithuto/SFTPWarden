from pathlib import Path

import pytest

from sftpwarden.config import ProviderType
from sftpwarden.providers import empty_provider_text, load_users
from sftpwarden.utils.errors import ProviderError


@pytest.mark.parametrize(
    ("kind", "document"),
    [
        (ProviderType.YAML, ""),
        (ProviderType.YAML, "# interrupted save\n"),
        (ProviderType.YAML, "{}"),
        (ProviderType.YAML, "users:"),
        (ProviderType.YAML, "users: false"),
        (ProviderType.YAML, "users: {}"),
        (ProviderType.YAML, "[]"),
        (ProviderType.YAML, "users: [invalid\n"),
        (ProviderType.CSV, ""),
        (ProviderType.CSV, "\n"),
        (ProviderType.CSV, "usernam"),
        (ProviderType.CSV, "username,username\n"),
        (ProviderType.CSV, "username,password_hash\nalice\n"),
        (ProviderType.CSV, 'username,comment\nalice,"unterminated\n'),
        (ProviderType.CSV, "username,uid\nalice,broken\n"),
    ],
)
def test_t09_incomplete_documents_are_not_empty_providers(
    tmp_path: Path, kind: ProviderType, document: str
) -> None:
    path = tmp_path / f"users.{kind.value}"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ProviderError):
        load_users(kind, path)


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV])
@pytest.mark.parametrize("schema", [1, 2])
def test_t09_explicit_empty_provider_remains_supported(
    tmp_path: Path, kind: ProviderType, schema: int
) -> None:
    path = tmp_path / f"users.{kind.value}"
    path.write_text(empty_provider_text(kind, user_schema=schema), encoding="utf-8")
    assert load_users(kind, path).users == []


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV])
def test_invalid_credentials_do_not_appear_in_provider_errors(
    tmp_path: Path, kind: ProviderType
) -> None:
    secret = "synthetic-sensitive-invalid-password"
    document = (
        f"users:\n- username: alice\n  password_hash: {secret}\n"
        if kind == ProviderType.YAML
        else f"username,password_hash\nalice,{secret}\n"
    )
    path = tmp_path / f"users.{kind.value}"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ProviderError) as error:
        load_users(kind, path)
    assert secret not in str(error.value)
