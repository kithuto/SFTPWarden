"""Malformed SQL rows cannot reactivate revoked access or advance applied state."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import SQLiteProvider
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey

from .conftest import CleanupStack, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


@pytest.mark.parametrize("field,invalid", [("disabled", "truue"), ("metadata", "not-json")])
def test_invalid_sql_policy_preserves_revocation_and_recovers(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack, field: str, invalid: str
) -> None:
    root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=False)
    cleanup_stack.add(server.close)
    path = server.inputs / "users.sqlite"
    provider = SQLiteProvider(
        config=ProviderConfig(type=ProviderType.SQLITE, user_schema=2), path=path
    )
    provider.write(
        ProviderUsers(
            schema_version=2,
            users=[
                SFTPUser(
                    username="alice",
                    keys=[SFTPUserKey(name="main", public_key=server.alice_public, disabled=True)],
                ),
                SFTPUser(
                    username="control",
                    keys=[SFTPUserKey(name="main", public_key=server.control_public)],
                ),
            ],
        )
    )
    config_path = server.inputs / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["provider"] = {"type": "sqlite", "path": "/inputs/users.sqlite", "user_schema": 2}
    config_path.write_text(yaml.safe_dump(config))
    server.start()
    server.assert_denied()
    state_command = ["docker", "exec", server.name, "cat", "/var/lib/sftpwarden/state.json"]
    original = run_external(state_command).stdout
    with closing(sqlite3.connect(path)) as connection, connection:
        assert field in {"disabled", "metadata"}
        connection.execute(
            f"update sftp_user_keys set {field} = ? where username = ?",  # noqa: S608
            (invalid, "alice"),
        )
    refresh = [
        "docker",
        "exec",
        server.name,
        "sftpwarden",
        "runtime",
        "refresh",
        "--config",
        "/inputs/config.yaml",
    ]
    result = run_external(refresh, check=False, timeout=30)
    effects = {
        "refresh_rejected": result.returncode != 0,
        "state_preserved": run_external(state_command).stdout == original,
        "alice_denied": "Permission denied (publickey)" in server.connect("alice").stderr,
        "control_authenticates": server.connect("control").returncode == 0,
    }
    assert all(effects.values()), effects
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "update sftp_user_keys set disabled = 0, metadata = '{}' where username = ?",
            ("alice",),
        )
    run_external(refresh, timeout=30)
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
