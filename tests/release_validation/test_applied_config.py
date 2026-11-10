from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from .conftest import CleanupStack, eventually, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_t14_refresh_does_not_apply_undeployed_auth_config(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack
) -> None:
    root = tmp_path / "runtime"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    server.start()
    original = run_external(["docker", "exec", server.name, "cat", "/etc/ssh/sshd_config"]).stdout
    config_path = server.inputs / "config.yaml"
    desired = yaml.safe_load(config_path.read_text())
    desired["auth"].update(allow_public_key=False, allow_password=True)
    config_path.write_text(yaml.safe_dump(desired))
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
        timeout=40,
    )
    assert (
        run_external(["docker", "exec", server.name, "cat", "/etc/ssh/sshd_config"]).stdout
        == original
    ), "User refresh rewrote undeployed daemon configuration"
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    run_external(["docker", "restart", server.name], timeout=40)
    server.refresh_port()
    eventually(
        lambda: server.connect("control").returncode == 0,
        timeout_seconds=60,
        description="restart with the existing applied configuration and host identity",
    )
    assert (
        run_external(["docker", "exec", server.name, "cat", "/etc/ssh/sshd_config"]).stdout
        == original
    )
    desired["provider"]["path"] = "/inputs/not-deployed.yaml"
    config_path.write_text(yaml.safe_dump(desired))
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
    )
    assert result.returncode != 0 and "requires deploy" in result.stdout
    config_path.write_text("invalid: [", encoding="utf-8")
    server.publish(revoked=True)
    eventually(
        lambda: "Permission denied (publickey)" in server.connect("alice").stderr,
        timeout_seconds=40,
        description="provider revocation while desired config is invalid",
    )
    server.assert_denied()
