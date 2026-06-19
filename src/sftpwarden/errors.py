from __future__ import annotations


class SFTPWardenError(Exception):
    """Base error with an optional practical fix suggestion."""

    def __init__(self, message: str, *, suggestion: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion


class ConfigError(SFTPWardenError):
    """Invalid project or global configuration."""


class ContextError(SFTPWardenError):
    """Context registry or resolution error."""


class ProviderError(SFTPWardenError):
    """Provider loading or mutation error."""


class RuntimeError(SFTPWardenError):
    """Runtime apply or refresh error."""
