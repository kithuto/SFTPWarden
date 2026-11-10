"""Upgrade and recovery use the published 1.3.0 image, never a relabeled candidate."""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

import pytest
import yaml

from sftpwarden.config import SFTPWardenConfig, default_project_config, load_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.render.compose import write_compose
from sftpwarden.services import backup_runtime
from sftpwarden.services.backup import restore_backup
from sftpwarden.services.backup_runtime import RuntimeCapture, capture_runtime, stopped_runtime
from sftpwarden.services.provider_layout import complete_provider_layout, migrate_provider_layout
from sftpwarden.services.restore_existing import verify_safety_archive
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.persistence import atomic_binary_output, atomic_bytes

from .conftest import free_tcp_port, remove_docker_container, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_fresh_recovery import transfer
from .test_watcher_ssh import build_ssh_delivery_project

LEGACY_IMAGE = (
    "ghcr.io/kithuto/sftpwarden:1.3.0@sha256:"
    "637d3976df81136294d7f5352d22afe0d5e43be58f8fc9e92b86aad41367b532"
)
LEGACY_COMMIT = "873010aa9ef657d7c2db45123e379c256acaa1f8"

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
    pytest.mark.release_upgrade,
]


@pytest.fixture(scope="session")
def legacy_image():
    before = run_external(["docker", "image", "inspect", LEGACY_IMAGE], check=False)
    try:
        if before.returncode:
            run_external(["docker", "pull", "--platform", "linux/amd64", LEGACY_IMAGE], timeout=300)
        result = run_external(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--entrypoint",
                "python",
                LEGACY_IMAGE,
                "-c",
                "import importlib.metadata; print(importlib.metadata.version('sftpwarden'))",
            ],
            timeout=30,
        )
        assert result.stdout.strip() == "1.3.0"
        yield LEGACY_IMAGE
    finally:
        if before.returncode:
            removed = run_external(["docker", "image", "rm", LEGACY_IMAGE], check=False, timeout=60)
            if removed.returncode and "No such image" not in removed.stderr:
                pytest.fail("The owned legacy image reference could not be removed.")


@pytest.mark.parametrize("schema", [1, 2])
def test_real_130_upgrade_failed_activation_and_recovery_point_rollback(
    tmp_path,
    monkeypatch,
    cleanup_stack,
    policy_image,
    legacy_image,
    schema,
):
    cleanup_stack.add(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(backup_runtime, "remote_preparation_image", lambda: policy_image)
    root = tmp_path / "original"
    client = tmp_path / "client"
    root.mkdir(mode=0o700)
    client.mkdir(mode=0o700)
    server = PolicyRuntime(client, legacy_image, sync=True)
    cleanup_stack.add(server.close)
    config = default_project_config(server.name, user_schema=schema)
    config.docker.image = legacy_image
    config.docker.container_name = server.name
    config.auth.allow_password = False
    config.server.host = "127.0.0.1"
    config.server.port = free_tcp_port()
    config.healthcheck.interval_seconds = 1
    config.sync.interval_seconds = 5
    write_config(root / "sftpwarden.yaml", config)
    provider_from_config(root, config).write(
        ProviderUsers(
            schema_version=schema,
            users=[
                SFTPUser(username="alice", uid=2101, gid=2101, public_keys=[server.alice_public]),
                SFTPUser(
                    username="control", uid=2102, gid=2102, public_keys=[server.control_public]
                ),
            ],
        )
    )
    for directory in ("data", "state", "host_keys"):
        (root / directory).mkdir(mode=0o700)
    # Render using the actual old package as well as running its actual image.
    run_external(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--volume",
            f"{root}:/project",
            "--entrypoint",
            "sftpwarden",
            legacy_image,
            "compose",
            "--config",
            "/project/sftpwarden.yaml",
            "--write",
        ],
        timeout=60,
    )
    artifact = root / config.docker.compose_file
    old_compose = yaml.safe_load(artifact.read_text())
    # Contain the old wildcard-binding defect inside this disposable local test.
    old_compose["services"]["sftpwarden"]["ports"] = [f"127.0.0.1:{config.server.port}:22"]
    # The legacy renderer runs as root. Replace its output atomically from the
    # operator-owned project directory rather than modifying a root-owned inode.
    atomic_bytes(artifact, yaml.safe_dump(old_compose).encode())
    command = ["docker", "compose", "-f", str(artifact)]
    cleanup_stack.add(lambda: run_external([*command, "down", "--timeout", "10"], timeout=60))
    run_external([*command, "up", "-d", "--wait", "--wait-timeout", "90"], timeout=120)
    server.refresh_port()
    public = run_external(
        [
            "docker",
            "exec",
            server.name,
            "cat",
            config.server.host_keys_dir + "/ssh_host_ed25519_key.pub",
        ]
    ).stdout.strip()
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    payload = client / "payload"
    payload.write_bytes(b"verified pre-upgrade recovery point\n")
    batch = client / "transfer.batch"
    batch.write_text(f'put "{payload.as_posix()}" upload/payload\nchmod 600 upload/payload\n')
    transfer(server, batch)

    def identities():
        public_keys = run_external(
            ["docker", "exec", server.name, "sh", "-c", "cat /etc/sftpwarden/host_keys/*.pub"]
        ).stdout
        # Public comments can change when validated pairs are reconstructed;
        # algorithm and complete key blob establish SSH identity.
        return {tuple(line.split()[:2]) for line in public_keys.splitlines()}

    before_keys = identities()
    info = json.loads(run_external(["docker", "inspect", server.name]).stdout)[0]
    # 1.3.0 has no applied-config snapshot. The maintenance procedure freezes its
    # verified mounted config; the helper also checks policy against retained state.
    frozen = tmp_path / "legacy-config.json"
    actual = run_external(
        [
            "docker",
            "exec",
            server.name,
            "python",
            "-c",
            (
                "import json,os; from sftpwarden.config import load_config; "
                "print(json.dumps({'format':1,'config':load_config(os.environ['SFTPWARDEN_CONFIG']).model_dump(mode='json')}))"
            ),
        ]
    ).stdout
    atomic_bytes(frozen, actual.encode())
    assert SFTPWardenConfig.model_validate(json.loads(actual)["config"]) == config
    runtime = RuntimeCapture(info["Id"], frozen, config, info)
    point = tmp_path / "verified-130.tgz"
    with atomic_binary_output(point) as stream, stopped_runtime(runtime):
        capture_runtime(runtime, stream, include_data=True)
    verified = verify_safety_archive(point)
    assert verified.users.schema_version == schema
    assert server.connect("control").returncode == 0

    upgraded = migrate_provider_layout(root, root / "sftpwarden.yaml")
    upgraded.docker.image = policy_image
    write_config(root / "sftpwarden.yaml", upgraded)
    write_compose(upgraded, root, allow_local_build=False)
    run_external(
        [*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90"], timeout=120
    )
    complete_provider_layout(root, upgraded)
    server.refresh_port()
    assert server.connect("alice").returncode == 0
    assert identities() == before_keys

    assert (
        run_external(
            [
                "docker",
                "exec",
                "--user",
                "2101:2101",
                server.name,
                "stat",
                "-c",
                "%u:%g:%a",
                "/data/alice/upload/payload",
            ]
        ).stdout.strip()
        == "2101:2101:600"
    )
    broken = yaml.safe_load(artifact.read_text())
    broken["services"]["sftpwarden"]["entrypoint"] = ["sh", "-c", "exit 1"]
    artifact.write_text(yaml.safe_dump(broken))
    failed = run_external(
        [*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "8"],
        check=False,
        timeout=30,
    )
    assert failed.returncode != 0
    run_external([*command, "down", "--timeout", "10"], timeout=60)

    recovered = tmp_path / "rollback"
    restore_backup(
        context_name=None, backup_path=str(point), target_root=str(recovered), include_data=True
    )
    rollback_config = load_config(recovered / "sftpwarden.yaml")
    assert rollback_config.docker.image == legacy_image
    rollback_context = local_context("rollback", recovered, rollback_config.provider.type)
    save_registry(ContextRegistry(default="rollback", contexts={"rollback": rollback_context}))
    rollback = ["docker", "compose", "-f", str(recovered / rollback_config.docker.compose_file)]
    cleanup_stack.add(lambda: run_external([*rollback, "down", "--timeout", "10"], timeout=60))
    run_external([*rollback, "create"], timeout=60)
    helper = server.name + "-metadata"
    cleanup_stack.add(lambda: remove_docker_container(helper))
    # Apply candidate recovery metadata before asking the old runtime to consume it.
    run_external(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            helper,
            "--network",
            "none",
            "--volumes-from",
            server.name,
            "--entrypoint",
            "python",
            policy_image,
            "-m",
            "sftpwarden.runtime.recovery",
        ],
        timeout=60,
    )
    run_external([*rollback, "up", "-d", "--wait", "--wait-timeout", "90"], timeout=120)
    server.refresh_port()
    assert server.connect("control").returncode == 0
    downloaded = client / "rollback-download"
    batch.write_text(f'get upload/payload "{downloaded.as_posix()}"\n')
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    assert identities() == before_keys


@pytest.mark.parametrize("storage", ["local-sync", "remote-only"])
def test_real_130_remote_upgrade_uses_explicit_ssh_deployment(
    tmp_path,
    monkeypatch,
    cleanup_stack,
    policy_image,
    legacy_image,
    storage,
):
    from sftpwarden.remote import deploy

    context, config, server, remote, local = build_ssh_delivery_project(
        tmp_path, monkeypatch, legacy_image, cleanup_stack
    )
    config.docker.image = policy_image
    for root in (local, remote):
        write_config(root / "sftpwarden.yaml", config)
    context.storage = storage
    if storage == "remote-only":
        context.root = context.config = ""
    monkeypatch.setattr(deploy, "remote_preparation_image", lambda: policy_image)

    def identities():
        public_keys = run_external(
            ["docker", "exec", server.name, "sh", "-c", "cat /etc/sftpwarden/host_keys/*.pub"]
        ).stdout
        return {tuple(line.split()[:2]) for line in public_keys.splitlines()}

    before = identities()
    previous_id = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout

    def runner(command, *, cwd=None):
        if command[0] == "ssh" and command[-1].endswith(" pull"):
            # The exact candidate already exists in the selected disposable daemon.
            command = [
                *command[:-1],
                "docker image inspect " + shlex.quote(policy_image) + " >/dev/null",
            ]
        run_external(command, cwd=Path(cwd) if cwd else None, timeout=180)

    deploy.deploy_context(context, runner=runner)
    server.refresh_port()
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    assert (
        run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
        != previous_id
    )
    assert identities() == before
    assert not (remote / ".sftpwarden-operation.lock").exists()


def test_incompatible_upgrade_requires_consent_then_deploys_all_contexts(
    tmp_path,
    monkeypatch,
    cleanup_stack,
    policy_image,
    legacy_image,
):
    """Use real old runtime evidence, then migrate only after expanded consent."""
    from typer.testing import CliRunner

    from sftpwarden.cli import app
    from sftpwarden.services import deploy as deployment
    from sftpwarden.services import update_artifacts, upgrade
    from sftpwarden.system.commands import CommandResult
    from sftpwarden.utils._version import get_version
    from sftpwarden.watcher.core import install_watcher

    from .conftest import docker_compose_down, docker_image_id, preserve_or_remove_image

    context, config, server, remote, local = build_ssh_delivery_project(
        tmp_path, monkeypatch, legacy_image, cleanup_stack
    )
    official = f"ghcr.io/kithuto/sftpwarden:{get_version()}"
    previous_tag = docker_image_id(official)
    cleanup_stack.add(lambda: preserve_or_remove_image(official, previous_tag))
    run_external(["docker", "tag", policy_image, official])
    config.docker.image = official
    provider = provider_from_config(local, config)
    users = provider.read()
    old_path = provider.path
    config.provider.path = "/etc/sftpwarden/users.yaml"
    old_path.replace(local / "users.yaml")
    write_config(context.config, config)
    second_root = tmp_path / "second"
    second_root.mkdir()
    second_config = default_project_config("all-contexts-second")
    second_config.docker.image = official
    second_config.docker.container_name = "sftpwarden-second-" + server.name.rsplit("-", 1)[-1]
    second_config.server.host = "127.0.0.1"
    second_config.server.port = free_tcp_port()
    second_config.healthcheck.interval_seconds = 1
    second_config.sync.interval_seconds = 5
    write_config(second_root / "sftpwarden.yaml", second_config)
    provider_from_config(second_root, second_config).write(users)
    second = local_context("second", second_root, second_config.provider.type)
    cleanup_stack.add(lambda: docker_compose_down(second_root))
    save_registry(ContextRegistry(contexts={context.name: context, second.name: second}))
    install_watcher(mode="systemd", activate=False)
    monkeypatch.setattr(
        upgrade,
        "discover_release",
        lambda **kwargs: {"status": "available", "version": get_version()},
    )

    # All execution is real. Only pulling the unpublished candidate is replaced
    # with verification of the exact image already loaded into this daemon.
    def runner(command, *, cwd=None, **kwargs):
        remote_command = command[0] == "ssh"
        if (
            (
                remote_command
                and (command[-1].endswith(" pull") or command[-1] == f"docker pull {official}")
            )
            or command[:3] == ["docker", "pull", official]
            or (command[:2] == ["docker", "compose"] and command[-1] == "pull")
        ):
            assert docker_image_id(official) == docker_image_id(policy_image)
            return CommandResult(command, 0, "candidate already loaded", "")
        result = run_external(command, cwd=Path(cwd) if cwd else None, timeout=180, check=False)
        return CommandResult(command, result.returncode, result.stdout, result.stderr)

    original_apply = deployment.apply_deployment_plan
    monkeypatch.setattr(
        deployment, "apply_deployment_plan", lambda entry: original_apply(entry, runner=runner)
    )
    monkeypatch.setattr(update_artifacts, "run", runner)
    before = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
    before_config = Path(context.config).read_bytes()
    declined = CliRunner().invoke(app, ["upgrade", "--yes", "--json"])
    assert declined.exit_code == 1, declined.output
    assert json.loads(declined.output)["status"] == "deploy_all_confirmation_required"
    assert Path(context.config).read_bytes() == before_config
    assert run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout == before
    assert not (second_root / "docker-compose.yml").exists()
    accepted = CliRunner().invoke(app, ["upgrade", "--yes", "--deploy-all", "--json"])
    assert accepted.exit_code == 0, accepted.output
    result = json.loads(accepted.output)
    assert result["deployments"]["status"] == "complete"
    assert {item["context"] for item in result["deployments"]["results"]} == {
        context.name,
        second.name,
    }
    assert all(
        item["applied"] == get_version() and item["status"] == "complete"
        for item in result["deployments"]["results"]
    )
    assert result["watcher"]["activated"] is False
    assert run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout != before
    assert (local / "provider/users.yaml").is_file()
    assert (second_root / "provider/users.yaml").is_file()
    server.refresh_port()
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
