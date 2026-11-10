"""Exercise remote-plan Compose commands locally; SSH transport is a separate gate."""

import shlex
import shutil
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.contexts import remote_context
from sftpwarden.remote.deploy import (
    complete_compose_deployment,
    deploy_plan,
    prepare_compose_deployment,
)
from sftpwarden.utils.persistence import atomic_bytes

from .conftest import CleanupStack, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_remote_compose_artifact_and_recreation_apply_config_with_stable_identity(
    tmp_path: Path,
    policy_image: str,
    cleanup_stack: CleanupStack,
) -> None:
    root = tmp_path / "remote project ; with spaces"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    config = default_project_config("remote-artifacts")
    config.docker.image = policy_image
    config.docker.container_name = server.name
    config.docker.compose_file = "local desired.yaml"
    config.auth.allow_password = False
    config.sync.enabled = True
    source = root / "sftpwarden.yaml"
    write_config(source, config)
    shutil.copyfile(server.inputs / "users.yaml", root / "users.yaml")
    for directory in ("data", "state", "host_keys"):
        (root / directory).mkdir(mode=0o700)
    context = remote_context(
        name="remote-artifacts",
        provider=ProviderType.YAML,
        remote_url=f"deploy@example.invalid:{root.as_posix()}",
        local_root=root,
        remote_root=root.as_posix(),
        remote_only=False,
        ssh_key=None,
        critical=False,
    )
    context.remote.compose_file = "remote runtime.yaml"
    prepare_compose_deployment(context)
    artifact = root / context.remote.compose_file
    model = yaml.safe_load(artifact.read_text())
    model["name"] = server.name
    model["services"]["sftpwarden"]["ports"] = ["127.0.0.1::22"]
    model["services"]["sftpwarden"]["healthcheck"]["interval"] = "1s"
    atomic_bytes(artifact, yaml.safe_dump(model).encode())
    remote_only = remote_context(
        name="remote-only",
        provider=ProviderType.YAML,
        remote_url=f"deploy@example.invalid:{root.as_posix()}",
        local_root=None,
        remote_root=root.as_posix(),
        remote_only=True,
        ssh_key=None,
        critical=False,
    )
    remote_only.remote.compose_file = context.remote.compose_file
    shell_command = next(
        command[-1] for command in deploy_plan(remote_only).commands if " up -d" in command[-1]
    )
    # Execute the exact Compose argv on the disposable local target, without claiming SSH coverage.
    arguments = shlex.split(shell_command)
    assert arguments[:3] == ["cd", root.as_posix(), "&&"]
    command = arguments[3:]
    cleanup_stack.add(
        lambda: run_external(
            ["docker", "compose", "-f", str(artifact), "down", "--volumes"],
            timeout=60,
        )
    )
    run_external(command, cwd=root, timeout=120)
    complete_compose_deployment(context)
    server.refresh_port()
    public = run_external(
        [
            "docker",
            "exec",
            server.name,
            "cat",
            "/etc/sftpwarden/host_keys/ssh_host_ed25519_key.pub",
        ]
    ).stdout.strip()
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("control").returncode == 0
    old_id = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
    before = run_external(["docker", "exec", server.name, "cat", "/etc/ssh/sshd_config"]).stdout
    assert "PasswordAuthentication no" in before

    desired = load_config(source)
    desired.auth.allow_password = True
    write_config(source, desired)
    run_external(command, cwd=root, timeout=120)
    new_id = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
    assert new_id != old_id
    after = run_external(["docker", "exec", server.name, "cat", "/etc/ssh/sshd_config"]).stdout
    assert "PasswordAuthentication yes" in after
    server.refresh_port()
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
