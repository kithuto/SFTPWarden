"""Refresh must reach the deployed runtime when the Compose filename is customized."""

from pathlib import Path

import pytest
import yaml

from sftpwarden.config import ProviderType
from sftpwarden.contexts import local_context
from sftpwarden.refresh.core import refresh_context
from sftpwarden.services.health import runtime_health_from_context
from sftpwarden.utils.errors import RuntimeError as RuntimeFailure

from .conftest import CleanupStack, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_custom_compose_filename_refresh_applies_revocation(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack
) -> None:
    root = tmp_path / "project with spaces"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=False)
    cleanup_stack.add(server.close)
    filename = "runtime compose.yaml"
    settings = yaml.safe_load((server.inputs / "config.yaml").read_text())
    settings["docker"] = {"compose_file": filename}
    source = root / "sftpwarden.yaml"
    source.write_text(yaml.safe_dump(settings))
    compose = {
        "name": server.name,
        "services": {
            "sftpwarden": {
                "image": policy_image,
                "container_name": server.name,
                "ports": ["127.0.0.1::22"],
                "healthcheck": {
                    "test": ["CMD", "sftpwarden", "runtime", "health"],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 60,
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(server.inputs),
                        "target": "/inputs",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(source),
                        "target": "/etc/sftpwarden/sftpwarden.yaml",
                        "read_only": True,
                    },
                ],
            }
        },
    }
    compose_path = root / filename
    compose_path.write_text(yaml.safe_dump(compose))
    command = ["docker", "compose", "-f", str(compose_path)]
    cleanup_stack.add(
        lambda: run_external([*command, "down", "--volumes", "--remove-orphans"], timeout=60)
    )
    run_external([*command, "up", "-d", "--wait", "--wait-timeout", "60"], timeout=90)
    server.refresh_port()
    public = run_external(
        ["docker", "exec", server.name, "cat", "/etc/sftpwarden/host_keys/ssh_host_ed25519_key.pub"]
    ).stdout.strip()
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    context = local_context("custom", root, ProviderType.YAML)
    server.publish(revoked=True)
    try:
        refresh_context(context)
        refreshed = True
    except RuntimeFailure:
        refreshed = False
    effects = {
        "refresh_succeeded": refreshed,
        "alice_denied": "Permission denied (publickey)" in server.connect("alice").stderr,
        "control_authenticates": server.connect("control").returncode == 0,
    }
    assert all(effects.values()), effects
    assert runtime_health_from_context(context)[0].status == "pass"
