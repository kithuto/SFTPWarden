from __future__ import annotations

import hmac

from rich.prompt import Prompt

from sftpwarden.config import ProviderType
from sftpwarden.contexts import remote_url_from_parts
from sftpwarden.security.passwords import resolve_password_hash
from sftpwarden.utils.dsn import build_sql_dsn, sql_default_port, sql_dsn_scheme
from sftpwarden.utils.errors import SFTPWardenError


def prompt_password_hash(
    *,
    password: str | None,
    password_hash: str | None,
    prompt_if_missing: bool = False,
) -> str | None:
    """Resolve password options into a stored password hash.

    Parameters
    ----------
    password
        Plaintext password provided through the CLI.
    password_hash
        Precomputed password hash provided through the CLI.
    prompt_if_missing
        Whether to prompt interactively when neither password option is set.

    Returns
    -------
    str or None
        Password hash ready to persist, or ``None`` when no password was provided.
    """
    if password is not None and password_hash is not None:
        return resolve_password_hash(password=password, password_hash=password_hash)
    if password is None and password_hash is None and prompt_if_missing:
        first = Prompt.ask("Password", password=True)
        second = Prompt.ask("Repeat password", password=True)
        if not hmac.compare_digest(first, second):
            raise SFTPWardenError("Passwords do not match.")
        password = first
    return resolve_password_hash(password=password, password_hash=password_hash)


def prompt_sql_dsn(provider: ProviderType) -> str:
    """Prompt for SQL connection fields and build a provider DSN.

    Parameters
    ----------
    provider
        SQL provider type.

    Returns
    -------
    str
        Conventional database URL for the selected provider.
    """
    scheme = sql_dsn_scheme(provider)
    port = sql_default_port(provider)
    host = Prompt.ask("SQL host", default="localhost")
    port_text = Prompt.ask("SQL port", default=str(port))
    database = Prompt.ask("SQL database", default="sftpwarden")
    username = Prompt.ask("SQL username", default="sftpwarden")
    password = Prompt.ask("SQL password", password=True, default="")
    return build_sql_dsn(
        scheme=scheme,
        username=username,
        password=password,
        host=host,
        port=int(port_text),
        database=database,
    )


def prompt_mongodb_dsn() -> str:
    """Prompt for a MongoDB DSN.

    Returns
    -------
    str
        MongoDB database URL.
    """
    return Prompt.ask("MongoDB DSN", default="mongodb://localhost:27017/sftpwarden")


def prompt_remote_url(
    *,
    host: str | None = None,
    remote_user: str | None = None,
    remote_root: str | None = None,
    default_remote_root: str = "~/sftpwarden",
) -> str:
    """Prompt for remote context fields and build a compact remote URL.

    Parameters
    ----------
    host
        Optional remote SSH host.
    remote_user
        Optional remote SSH user.
    remote_root
        Optional remote project root.
    default_remote_root
        Default remote root used when prompting.

    Returns
    -------
    str
        URL in ``user@host:/path`` form.
    """
    final_host = host or Prompt.ask("Remote host")
    final_user = remote_user or Prompt.ask("Remote user")
    final_remote_root = remote_root or Prompt.ask("Remote root", default=default_remote_root)
    if final_remote_root is None:
        raise SFTPWardenError("Remote root is required.")
    return remote_url_from_parts(
        host=final_host,
        remote_root=final_remote_root,
        remote_user=final_user,
    )
