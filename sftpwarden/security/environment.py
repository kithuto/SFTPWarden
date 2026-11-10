"""Resolve only explicit provider environment references, once, on the owning host."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from sftpwarden.utils.errors import ProviderError

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_REFERENCE = re.compile(r"\$\{(" + _NAME + r")\}")
_WHOLE_REFERENCE = re.compile(r"\$(" + _NAME + r")|%(" + _NAME + r")%")


def dsn_environment_names(value: str) -> tuple[str, ...]:
    """Return explicit reference names without reading or disclosing their values."""
    whole = _WHOLE_REFERENCE.fullmatch(value)
    if whole:
        return (next(name for name in whole.groups() if name is not None),)
    if "${" in _REFERENCE.sub("", value):
        raise ProviderError("Invalid provider DSN environment reference.")
    return tuple(dict.fromkeys(_REFERENCE.findall(value)))


def resolve_provider_dsn(value: str, *, environment: Mapping[str, str] | None = None) -> str:
    """Expand explicit references once; preserve dollar signs within literal credentials."""
    environment = os.environ if environment is None else environment
    names = dsn_environment_names(value)
    for name in names:
        if not environment.get(name):
            raise ProviderError(
                f"Provider environment variable {name} is missing or empty.",
                suggestion="Set it on the machine executing the provider operation.",
            )
    if _WHOLE_REFERENCE.fullmatch(value):
        return environment[names[0]]
    return _REFERENCE.sub(lambda match: environment[match.group(1)], value)
