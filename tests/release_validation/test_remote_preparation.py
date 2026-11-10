"""Execute remote POSIX preparation commands locally against disposable Docker.

SSH transport and registry pulls are separate release gates. This scenario uses
the freshly built candidate image and the exact preparation/recreation commands.
"""

import pytest

from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.contexts import remote_context
from sftpwarden.providers import SQLiteProvider, provider_from_config
from sftpwarden.remote import deploy
from sftpwarden.remote.preparation import PREPARATION_FILE
from sftpwarden.render.compose import write_compose
from sftpwarden.services.users import UserService
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.utils.persistence import atomic_bytes

from .conftest import eventually, free_tcp_port, require_executable, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_remote_preparation_migrates_running_legacy_sqlite_and_applies_atomic_edits(
    tmp_path,
    monkeypatch,
    policy_image,
    cleanup_stack,
):
    require_executable("sh")
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project 'quote, and spaces"
    cleanup_stack.add(lambda: cleanup_stack.remove_tree(root))
    root.mkdir(mode=0o700)
    server = PolicyRuntime(root, policy_image, sync=True)
    cleanup_stack.add(server.close)
    config = default_project_config("remote-preparation", ProviderType.SQLITE, user_schema=1)
    config.docker.image = policy_image
    config.docker.container_name = server.name
    config.project.name = server.name
    config.server.host = "127.0.0.1"
    config.server.port = free_tcp_port()
    config.healthcheck.interval_seconds = 1
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
    old_artifact = write_compose(config, root, allow_local_build=False)
    cleanup_stack.add(
        lambda: run_external(
            [
                "docker",
                "compose",
                "-f",
                str(old_artifact),
                "down",
                "--volumes",
            ],
            timeout=60,
        )
    )
    run_external(
        [
            "docker",
            "compose",
            "-f",
            str(old_artifact),
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "90",
        ],
        timeout=120,
    )
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
    run_external(
        [
            "docker",
            "exec",
            server.name,
            "sh",
            "-c",
            "printf preserved > /data/alice/upload/keep",
        ]
    )
    config.provider.user_schema = 2
    write_config(path, config)
    context = remote_context(
        name="remote-preparation",
        provider=ProviderType.SQLITE,
        remote_url=f"deploy@example.invalid:{root.as_posix()}",
        local_root=None,
        remote_root=root.as_posix(),
        remote_only=True,
        ssh_key=None,
        critical=False,
    )
    context.remote.compose_file = "remote runtime.yml"
    monkeypatch.setattr(deploy, "remote_preparation_image", lambda: policy_image)
    artifact = root / context.remote.compose_file
    cleanup_stack.add(
        lambda: run_external(
            [
                "docker",
                "compose",
                "-f",
                str(artifact),
                "down",
                "--volumes",
            ],
            timeout=60,
            check=False,
        )
    )
    for command in deploy.deploy_plan(context).commands:
        shell_command = command[-1]
        if shell_command.endswith(" pull"):
            continue  # The candidate is built locally; registry delivery is not this test's gate.
        run_external(["sh", "-c", shell_command], timeout=120)
    assert not (root / PREPARATION_FILE).exists()
    current = load_config(path)
    assert current.provider.path.startswith(f"{CONTAINER_PROVIDER_DATA_DIR}/")
    assert current.provider.user_schema == 2
    server.refresh_port()
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    assert (
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "cat",
                "/data/alice/upload/keep",
            ]
        ).stdout
        == "preserved"
    )
    container_id = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
    service = UserService(config_path=str(path))
    service.disable_user_key("alice", service.list_user_keys("alice")[0].name, disabled=True)

    def revoked():
        result = server.connect("alice")
        return result.returncode != 0 and "Permission denied (publickey)" in result.stderr

    eventually(revoked, timeout_seconds=30, description="revocation through migrated remote mount")
    server.assert_denied()
    provider = provider_from_config(root, current)
    restored = provider.read()
    restored.users[0].keys[0].disabled = False
    staging = root / "atomic-replacement.sqlite"
    SQLiteProvider(current.provider, path=staging).write(restored)
    atomic_bytes(provider.path, staging.read_bytes())
    eventually(
        lambda: server.connect("alice").returncode == 0,
        timeout_seconds=30,
        description="atomic provider replacement visible without deploy",
    )
    assert server.connect("control").returncode == 0
    assert run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout == (
        container_id
    )
