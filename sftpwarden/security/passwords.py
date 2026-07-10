from __future__ import annotations

import hmac
from typing import Protocol, cast

from passlib.hash import sha512_crypt

from sftpwarden.utils.errors import ProviderError


class _PasswordHasher(Protocol):
    """Typed subset of a configured Passlib password hasher."""

    def hash(self, secret: str) -> str:
        """Hash one plaintext secret."""
        ...


class _PasswordHasherFactory(Protocol):
    """Typed subset of the dynamic Passlib handler factory."""

    def using(self, *, rounds: int) -> _PasswordHasher:
        """Return a password hasher configured with explicit rounds."""
        ...


def hash_password(password: str) -> str:
    """Hash a plaintext password for system authentication.

    Parameters
    ----------
    password
        Plaintext password.

    Returns
    -------
    str
        SHA-512 crypt password hash.

    Raises
    ------
    ProviderError
        Raised when the password is empty or too short.
    """
    if not password:
        raise ProviderError("Password cannot be empty.")
    if len(password) < 8:
        raise ProviderError(
            "Password must be at least 8 characters long.",
            suggestion="Use a longer password or pass an existing hash with --password-hash.",
        )
    hasher_factory = cast(_PasswordHasherFactory, sha512_crypt)
    return hasher_factory.using(rounds=500_000).hash(password)


def resolve_password_hash(*, password: str | None, password_hash: str | None) -> str | None:
    """Resolve mutually exclusive plaintext and precomputed password inputs.

    Parameters
    ----------
    password
        Optional plaintext password to hash.
    password_hash
        Optional precomputed system password hash.

    Returns
    -------
    str | None
        Password hash to store, or ``None`` when no password was supplied.

    Raises
    ------
    ProviderError
        Raised when both inputs are supplied or the hash is malformed.
    """
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
