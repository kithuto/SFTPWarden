"""Schema promotion must preserve policy through actual Compose mounts and SFTP."""

import pytest
import yaml

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import local_context
from sftpwarden.providers import provider_from_config
from sftpwarden.remote.deploy import complete_compose_deployment, prepare_compose_deployment
from sftpwarden.services.users import UserService
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.persistence import atomic_bytes

from .conftest import run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


@pytest.mark.parametrize("kind", [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE])
def test_migration_requires_deploy_then_revokes_only_selected_key(
    tmp_path,
    monkeypatch,
    policy_image,
    cleanup_stack,
    kind,
):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    config = default_project_config("schema-live", kind, user_schema=1)
    config.docker.image = policy_image
    config.docker.container_name = server.name
    config.sync.interval_seconds = 5
    config.auth.allow_password = False
    path = root / "sftpwarden.yaml"
    write_config(path, config)
    provider_from_config(root, config).write(
        ProviderUsers(
            schema_version=1,
            users=[
                SFTPUser(username="alice", public_keys=[server.alice_public]),
                SFTPUser(username="control", public_keys=[server.control_public]),
            ],
        )
    )
    for directory in ("data", "state", "host_keys"):
        (root / directory).mkdir(mode=0o700)
    entry = local_context("schema-live", root, kind)
    prepare_compose_deployment(entry)
    artifact = root / config.docker.compose_file
    model = yaml.safe_load(artifact.read_text())
    model["name"] = server.name
    model["services"]["sftpwarden"]["ports"] = ["127.0.0.1::22"]
    model["services"]["sftpwarden"]["healthcheck"]["interval"] = "1s"
    atomic_bytes(artifact, yaml.safe_dump(model).encode())
    base = ["docker", "compose", "-f", str(artifact)]
    cleanup_stack.add(lambda: run_external([*base, "down", "--volumes"], timeout=60))
    up = [*base, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90"]
    run_external(up, timeout=120)
    complete_compose_deployment(entry)
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
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    service = UserService(config_path=str(path))
    service.disable_user_key(
        "alice",
        service.list_user_keys("alice")[0].name,
        disabled=True,
        allow_migration=True,
    )
    failed_refresh = run_external(
        [
            "docker",
            "exec",
            server.name,
            "python",
            "-c",
            "from sftpwarden.runtime.core import apply_once; apply_once()",
        ],
        check=False,
        timeout=30,
    )
    assert failed_refresh.returncode != 0
    assert "schema differs" in failed_refresh.stdout + failed_refresh.stderr
    # Mismatch preserves the applied policy until the explicitly required deploy.
    assert server.connect("control").returncode == 0
    old_id = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
    run_external(up, timeout=120)
    assert run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout != old_id
    server.refresh_port()  # Retain the original trusted host key after port allocation changes.
    server.assert_denied()
