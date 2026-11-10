"""Malformed file authorization fields must preserve the applied policy."""

import csv
import io
import json
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import CSVProvider
from sftpwarden.users.schemas import users_from_mapping

from .conftest import CleanupStack, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


@pytest.mark.parametrize("invalid", ["csv_boolean", "csv_duplicate_key_field", "yaml_null_keys"])
def test_invalid_file_policy_preserves_authorization_and_recovers(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack, invalid: str
) -> None:
    root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=False)
    cleanup_stack.add(server.close)
    initially_enabled = invalid == "yaml_null_keys"
    if invalid == "csv_boolean":
        server.publish(disabled=True)
    elif invalid == "csv_duplicate_key_field":
        server.publish(revoked=True)
    path = server.inputs / "users.yaml"
    original_document = yaml.safe_load(path.read_text())
    if invalid.startswith("csv"):
        path = server.inputs / "users.csv"
        CSVProvider(config=ProviderConfig(type=ProviderType.CSV, user_schema=2), path=path).write(
            users_from_mapping(original_document)
        )
        config_path = server.inputs / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["provider"] = {"type": "csv", "path": "/inputs/users.csv", "user_schema": 2}
        config_path.write_text(yaml.safe_dump(config))
    original = path.read_bytes()
    server.start()
    if initially_enabled:
        assert server.connect("alice").returncode == 0
    else:
        server.assert_denied()
    state_command = ["docker", "exec", server.name, "cat", "/var/lib/sftpwarden/state.json"]
    state = run_external(state_command).stdout
    if invalid.startswith("csv"):
        reader = csv.DictReader(io.StringIO(path.read_text()))
        fields, rows = reader.fieldnames, list(reader)
        if invalid == "csv_boolean":
            rows[0]["disabled"] = "truue"
        else:
            rows[0]["keys"] = (
                '[{"name":"only","public_key":'
                + json.dumps(server.alice_public)
                + ',"disabled":true,"disabled":false}]'
            )
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        malformed = output.getvalue()
    else:
        original_document["users"][0]["keys"] = None
        malformed = yaml.safe_dump(original_document)
    candidate = path.with_suffix(".candidate")
    candidate.write_text(malformed)
    candidate.replace(path)
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
    alice = server.connect("alice")
    effects = {
        "refresh_rejected": result.returncode != 0,
        "state_preserved": run_external(state_command).stdout == state,
        "authorization_preserved": alice.returncode == 0
        if initially_enabled
        else "Permission denied (publickey)" in alice.stderr,
        "control_authenticates": server.connect("control").returncode == 0,
    }
    assert all(effects.values()), effects
    candidate.write_bytes(original)
    candidate.replace(path)
    run_external(refresh, timeout=30)
    if initially_enabled:
        assert server.connect("alice").returncode == 0
    else:
        server.assert_denied()


def test_explicit_empty_named_keys_revoke_legacy_copy(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack
) -> None:
    root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=False)
    cleanup_stack.add(server.close)
    path = server.inputs / "users.yaml"
    document = yaml.safe_load(path.read_text())
    document["users"][0]["public_keys"] = [server.alice_public]
    path.write_text(yaml.safe_dump(document))
    server.start()
    assert server.connect("alice").returncode == 0
    document["users"][0]["keys"] = []
    temporary = path.with_suffix(".candidate")
    temporary.write_text(yaml.safe_dump(document))
    temporary.replace(path)
    run_external(
        [
            "docker",
            "exec",
            server.name,
            "sftpwarden",
            "runtime",
            "refresh",
            "--config",
            "/inputs/config.yaml",
        ],
        timeout=30,
    )
    server.assert_denied()
