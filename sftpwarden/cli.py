from __future__ import annotations

# Import command modules for Typer registration side effects.
from sftpwarden.cli_commands import config as _config  # noqa: F401
from sftpwarden.cli_commands import context as _context  # noqa: F401
from sftpwarden.cli_commands import core as _core  # noqa: F401
from sftpwarden.cli_commands import init as _init  # noqa: F401
from sftpwarden.cli_commands import provider as _provider  # noqa: F401
from sftpwarden.cli_commands import runtime as _runtime  # noqa: F401
from sftpwarden.cli_commands import users as _users  # noqa: F401
from sftpwarden.cli_commands import watcher as _watcher  # noqa: F401
from sftpwarden.cli_commands.app import app
from sftpwarden.cli_commands.errors import install_cli_error_handlers

install_cli_error_handlers(app)

__all__ = ["app"]
