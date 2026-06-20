from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sftpwarden.config import SFTPWardenConfig, WatcherConfig, WatcherMode, load_config
from sftpwarden.runtime import render_sshd_config_text
from sftpwarden.utils.errors import ConfigError


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_rejects_server_container_port(tmp_path: Path) -> None:
    config_path = tmp_path / "sftpwarden.yaml"
    write_yaml(
        config_path,
        {
            "version": 1,
            "project": {"name": "dev"},
            "server": {"port": 2222, "container_port": 2022},
            "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
        },
    )

    with pytest.raises(ConfigError, match="container_port"):
        load_config(config_path)


def test_minimum_config_requires_project_name() -> None:
    with pytest.raises(ValidationError):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {},
                "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
            }
        )


def test_watcher_rejects_include_and_exclude_keys() -> None:
    with pytest.raises(ValidationError):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "dev"},
                "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
                "watcher": {
                    "enabled": True,
                    "mode": "systemd",
                    "include": ["users.yaml"],
                    "exclude": [],
                },
            }
        )


def test_systemd_watcher_does_not_allow_image() -> None:
    with pytest.raises(ValidationError):
        WatcherConfig(enabled=True, mode=WatcherMode.SYSTEMD, image="sftpwarden-watcher:local")


def test_docker_watcher_can_default_image() -> None:
    watcher = WatcherConfig(enabled=True, mode=WatcherMode.DOCKER)

    assert watcher.image == "sftpwarden-watcher:local"


def test_password_authentication_is_enabled_by_default() -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
        }
    )

    assert config.auth.allow_password is True
    assert config.auth.recommended == "password"
    assert "PasswordAuthentication yes" in render_sshd_config_text(config)
    assert "UsePAM" not in render_sshd_config_text(config)
    assert "GSSAPIAuthentication" not in render_sshd_config_text(config)


def test_password_authentication_can_be_disabled_for_key_only() -> None:
    config = SFTPWardenConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "dev"},
            "auth": {"allow_password": False, "allow_public_key": True},
            "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
        }
    )

    rendered = render_sshd_config_text(config)

    assert "PasswordAuthentication no" in rendered
    assert "PubkeyAuthentication yes" in rendered


def test_provider_path_rejects_traversal() -> None:
    with pytest.raises(ValidationError):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "dev"},
                "provider": {"type": "yaml", "path": "../users.yaml"},
            }
        )


def test_upload_dir_rejects_traversal() -> None:
    with pytest.raises(ValidationError):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "dev"},
                "isolation": {"upload_dir": "../upload"},
                "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
            }
        )


def test_rejects_unsafe_chroot_root_permissions() -> None:
    with pytest.raises(ValidationError, match="root_permissions"):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "dev"},
                "isolation": {"root_permissions": "777"},
                "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
            }
        )


def test_rejects_world_writable_upload_permissions() -> None:
    with pytest.raises(ValidationError, match="upload_permissions"):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "dev"},
                "isolation": {"upload_permissions": "777"},
                "provider": {"type": "yaml", "path": "/etc/sftpwarden/users.yaml"},
            }
        )


def test_rejects_mutating_sql_provider_query() -> None:
    with pytest.raises(ValidationError, match="read-only|SELECT"):
        SFTPWardenConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "dev"},
                "provider": {
                    "type": "mysql",
                    "dsn": "mysql://user:pass@localhost/sftp",
                    "query": "delete from sftp_users",
                },
            }
        )


@pytest.mark.parametrize(
    "path",
    [
        Path("examples/yaml/sftpwarden.yaml"),
        Path("examples/csv/sftpwarden.yaml"),
        Path("examples/mysql/sftpwarden.yaml"),
        Path("examples/postgres/sftpwarden.yaml"),
    ],
)
def test_examples_validate(path: Path) -> None:
    assert load_config(path).project.name
