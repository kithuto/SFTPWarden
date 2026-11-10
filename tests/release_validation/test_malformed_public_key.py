"""Malformed SSH key input must preserve the last usable authorization."""

import base64
import json
from pathlib import Path

import pytest
import yaml

from .conftest import CleanupStack, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


@pytest.mark.parametrize("invalid", ["type_mismatch", "truncated", "compressed_ec"])
def test_malformed_key_preserves_authentication_and_applied_state(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack, invalid: str
) -> None:
    root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=False)
    cleanup_stack.add(server.close)
    server.start()
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0

    def snapshot(path: str) -> str:
        return run_external(["docker", "exec", server.name, "cat", path]).stdout

    state_path = "/var/lib/sftpwarden/state.json"
    keys_path = "/etc/sftpwarden/authorized_keys/alice"
    state, authorized = snapshot(state_path), snapshot(keys_path)
    path = server.inputs / "users.yaml"
    document = yaml.safe_load(path.read_text())
    if invalid == "type_mismatch":
        malformed = "ssh-rsa " + server.alice_public.split()[1]
    elif invalid == "truncated":
        blob = base64.b64decode(server.alice_public.split()[1])[:-1]
        malformed = "ssh-ed25519 " + base64.b64encode(blob).decode()
    else:
        fixtures = json.loads(
            (Path(__file__).parents[1] / "fixtures/ssh_public_keys.json").read_text()
        )
        blob = base64.b64decode(fixtures["nistp256"]["public_key"].split()[1])
        point = blob[-65:]
        compressed = bytes([2 + (point[-1] & 1)]) + point[1:33]
        malformed = (
            "ecdsa-sha2-nistp256 "
            + base64.b64encode(
                blob[:-69] + len(compressed).to_bytes(4, "big") + compressed
            ).decode()
        )
    document["users"][0]["keys"][0]["public_key"] = malformed
    candidate = path.with_suffix(".next")
    candidate.write_text(yaml.safe_dump(document))
    candidate.replace(path)
    result = run_external(
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
        check=False,
        timeout=30,
    )
    # Gather all effects before asserting, making the baseline failure diagnostic useful.
    effects = {
        "refresh_rejected": result.returncode != 0,
        "state_preserved": snapshot(state_path) == state,
        "authorization_preserved": snapshot(keys_path) == authorized,
        "alice_authenticates": server.connect("alice").returncode == 0,
        "control_authenticates": server.connect("control").returncode == 0,
    }
    assert all(effects.values()), effects
    assert "Runtime reconciliation failed" in result.stdout + result.stderr
    server.publish()
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
    assert server.connect("alice").returncode == 0


@pytest.mark.parametrize(
    "kind,bits", [("rsa", "2048"), ("ecdsa", "256"), ("ecdsa", "384"), ("ecdsa", "521")]
)
def test_valid_rsa_and_ecdsa_keys_authenticate(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack, kind: str, bits: str
) -> None:
    root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=False)
    cleanup_stack.add(server.close)
    # The fixture initially makes an Ed25519 pair; replace only the owned Alice pair.
    server.alice.unlink()
    server.alice.with_suffix(".pub").unlink()
    run_external(["ssh-keygen", "-q", "-t", kind, "-b", bits, "-N", "", "-f", str(server.alice)])
    server.alice_public = server.alice.with_suffix(".pub").read_text().strip()
    server.publish()
    server.start()
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
