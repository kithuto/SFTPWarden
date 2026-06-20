from __future__ import annotations

import hashlib
import hmac

import yaml

from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


def upsert_user(users: ProviderUsers, user: SFTPUser) -> ProviderUsers:
    """Return a user set with one user created or replaced.

    Parameters
    ----------
    users
        Existing provider users.
    user
        User to add or replace.

    Returns
    -------
    ProviderUsers
        Updated users sorted by username.
    """
    next_users = [existing for existing in users.users if existing.username != user.username]
    next_users.append(user)
    next_users.sort(key=lambda item: item.username)
    return ProviderUsers(users=next_users)


def remove_user(users: ProviderUsers, username: str) -> ProviderUsers:
    """Return a user set without a username.

    Parameters
    ----------
    users
        Existing provider users.
    username
        Username to remove.

    Returns
    -------
    ProviderUsers
        Updated users.

    Raises
    ------
    ProviderError
        Raised when the user does not exist.
    """
    next_users = [existing for existing in users.users if existing.username != username]
    if len(next_users) == len(users.users):
        raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")
    return ProviderUsers(users=next_users)


def find_user(users: ProviderUsers, username: str) -> SFTPUser:
    """Find a user by username.

    Parameters
    ----------
    users
        Provider users to search.
    username
        Username to find.

    Returns
    -------
    SFTPUser
        Matching user.

    Raises
    ------
    ProviderError
        Raised when the user does not exist.
    """
    for user in users.users:
        if hmac.compare_digest(user.username, username):
            return user
    raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")


def users_fingerprint(users: ProviderUsers) -> str:
    """Build a stable runtime fingerprint for provider users.

    Parameters
    ----------
    users
        Provider users to fingerprint.

    Returns
    -------
    str
        SHA-256 fingerprint that ignores non-runtime metadata.
    """
    canonical = yaml.safe_dump(
        {
            "users": [
                user.model_dump(mode="json", exclude_none=True, exclude={"comment"})
                for user in users.users
            ]
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
