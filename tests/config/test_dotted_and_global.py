from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.system.commands as command_services
from sftpwarden.config import ProviderType
from sftpwarden.config.global_config import load_global_config, resolve_provider, save_global_config
from sftpwarden.utils.dotted import format_value, get_dotted, parse_cli_value, set_dotted
from sftpwarden.utils.errors import ConfigError, RuntimeError
from sftpwarden.utils.paths import (
    app_home,
    contexts_path,
    ensure_parent,
    global_config_path,
    project_config_path,
)

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


def test_dotted_and_path_utilities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"server": {"port": 2222}, "flags": {"enabled": True}, "items": [1, 2]}
    set_dotted(data, "server.port", parse_cli_value("2200"))

    assert get_dotted(data, "server.port") == 2200
    assert format_value(True) == "true"
    assert format_value([1, 2]) == "- 1\n- 2"
    with pytest.raises(ConfigError, match="Unknown configuration path"):
        get_dotted(data, "missing.value")
    with pytest.raises(ConfigError, match="Unknown configuration path"):
        set_dotted(data, "server.missing", 1)

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    target = tmp_path / "nested" / "file.txt"
    ensure_parent(target)
    assert target.parent.is_dir()
    assert app_home() == tmp_path / "home"
    assert global_config_path() == tmp_path / "home" / "config.toml"
    assert contexts_path() == tmp_path / "home" / "contexts.toml"
    assert project_config_path(tmp_path / "project") == tmp_path / "project" / "sftpwarden.yaml"


def test_global_config_and_command_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad_config = tmp_path / "bad.toml"
    bad_config.write_text("not = [valid\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid global config"):
        load_global_config(bad_config)

    monkeypatch.setenv("SFTPWARDEN_DEFAULT_PROVIDER", "csv")
    assert resolve_provider() == ProviderType.CSV
    monkeypatch.delenv("SFTPWARDEN_DEFAULT_PROVIDER")
    saved = tmp_path / "config.toml"
    config = load_global_config()
    config.default_provider = ProviderType.CSV
    save_global_config(config, saved)
    assert load_global_config(saved).default_provider == ProviderType.CSV

    def missing_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("missing-binary")

    monkeypatch.setattr(command_services.subprocess, "run", missing_run)
    result = command_services.run(["definitely-not-a-real-sftpwarden-binary"])
    assert result.returncode == 127
    monkeypatch.setattr(
        command_services,
        "run",
        lambda *_args, **_kwargs: command_services.CommandResult(
            args=["bad"], returncode=2, stdout="", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="failed"):
        command_services.run_checked(
            ["definitely-not-a-real-sftpwarden-binary"],
            error_type=RuntimeError,
            message="failed",
            fallback_suggestion="fallback",
        )
