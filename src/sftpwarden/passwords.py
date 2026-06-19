from __future__ import annotations

import hmac
from passlib.hash import sha512_crypt

from sftpwarden.errors import ProviderError


def hash_password(password: str) -> str:
    if not password:
        raise ProviderError("Password cannot be empty.")
    if len(password) < 8:
        raise ProviderError(
            "Password must be at least 8 characters long.",
            suggestion="Use a longer password or pass an existing hash with --password-hash.",
        )
    return sha512_crypt.using(rounds=500_000).hash(password)


def resolve_password_hash(*, password: str | None, password_hash: str | None) -> str | None:
    if password is not None and password_hash is not None:
        raise ProviderError(
            "Use either --password or --password-hash, not both.",
            suggestion=(
                "Pass plaintext with --password or a precomputed shadow hash with --password-hash."
            ),
        )
    if password is not None:
        return hash_password(password)
    if password_hash is None:
        return None
    if hmac.compare_digest(password_hash, password_hash.strip()):
        return password_hash
    raise ProviderError("password_hash cannot contain leading or trailing whitespace.")
