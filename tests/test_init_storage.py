from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.cli_commands.init as init_commands
import sftpwarden.cli_commands.prompts as prompt_commands
from sftpwarden.config import ProviderType, default_project_config
from sftpwarden.utils.errors import SFTPWardenError


def test_mongodb_dsn_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive MongoDB init asks for one MongoDB DSN."""
    monkeypatch.setattr(
        prompt_commands.Prompt, "ask", lambda *_args, **_kwargs: "mongodb://db/sftp"
    )

    assert prompt_commands.prompt_mongodb_dsn() == "mongodb://db/sftp"


def test_remote_local_sync_init_creates_sqlite_provider_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote local-sync init seeds SQLite as a local syncable provider file."""
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(init_commands, "register_context", lambda _entry: None)
    monkeypatch.setattr(init_commands, "set_default_context", lambda _name: None)
    monkeypatch.setattr(init_commands, "install_context_watcher", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(init_commands, "print_success", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(init_commands, "print_info", lambda *_args, **_kwargs: None)
    root = tmp_path / "sqlite-remote"

    init_commands.init_remote_context(
        name="prod",
        provider="sqlite",
        root=str(root),
        remote_url="deploy@example.com:/opt/sftpwarden",
        dsn=None,
        query=None,
        table="sftp_users",
        collection="sftp_users",
        create_table=None,
        host=None,
        remote_user=None,
        port=None,
        remote_root=None,
        ssh_key=None,
        watcher_mode=None,
        remote_only=False,
        skip_checks=True,
        critical=True,
        yes=True,
    )

    assert (root / "users.sqlite").exists()


def test_init_aborts_when_mongodb_collection_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Init reports MongoDB collection storage distinctly when creation is refused."""

    class MissingStorageProvider:
        def table_exists(self) -> bool:
            return False

        def create_table(self) -> None:
            raise AssertionError("create_table should not run")

    config = default_project_config(
        "dev",
        ProviderType.MONGODB,
        dsn="mongodb://localhost:27017/sftp",
        collection="accounts",
    )
    monkeypatch.setattr(
        init_commands,
        "provider_from_config",
        lambda *_args, **_kwargs: MissingStorageProvider(),
    )

    with pytest.raises(SFTPWardenError, match="MongoDB collection does not exist"):
        init_commands.ensure_provider_storage_for_init(
            tmp_path,
            config,
            create_storage=False,
            yes=False,
        )
