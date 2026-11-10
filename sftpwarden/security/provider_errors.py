"""Keep driver diagnostics and their exception chains behind the provider boundary."""

from __future__ import annotations

import ssl
from collections.abc import Callable, Iterator
from functools import wraps
from typing import ParamSpec, TypeVar

from sftpwarden.utils.errors import ProviderError

_P = ParamSpec("_P")
_T = TypeVar("_T")


def private_database_errors(operation: Callable[_P, _T]) -> Callable[_P, _T]:
    """Expose controlled diagnostics after operation cleanup, without driver text."""

    @wraps(operation)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return operation(*args, **kwargs)
        except ProviderError as exc:
            # Preserve controlled domain errors, but suppress nested driver causes.
            raise ProviderError(exc.message, suggestion=exc.suggestion) from None
        except Exception as exc:
            raise _safe_database_error(exc) from None

    return guarded


def _safe_database_error(error: Exception) -> ProviderError:
    errors = tuple(_exception_chain(error))
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in errors):
        message = "Database TLS certificate verification failed."
    elif any(
        isinstance(item, TimeoutError)
        or getattr(item, "timeout", False) is True
        or (
            type(item).__name__ == "ConnectionTimeout"
            and type(item).__module__.startswith("psycopg")
        )
        for item in errors
    ):
        message = "Database provider operation timed out."
    else:
        message = "Database provider operation failed."
    return ProviderError(
        message,
        suggestion=(
            "Check database availability, credentials, TLS settings and server-side logs. "
            "Inspect current provider data before retrying a mutation."
        ),
    )


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for nested in (current.__cause__, current.__context__):
            if nested is not None:
                pending.append(nested)
