"""Real SSH delivery, truncated input and retry through the actual runtime mount."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import PurePosixPath
from uuid import uuid4

import pytest
import yaml

from sftpwarden.config import default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, remote_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.refresh import core as refresh_core
from sftpwarden.remote import checks as remote_checks
from sftpwarden.remote import deploy as remote_deploy
from sftpwarden.remote import environment as remote_environment
from sftpwarden.remote import locking as remote_locking
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.render.compose import write_compose
from sftpwarden.services import backup_remote, deployment_upgrade, restore_remote, update_artifacts
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey
from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.watcher import delivery, polling
from sftpwarden.watcher.backends import docker as docker_watcher
from sftpwarden.watcher.backends import systemd as systemd_watcher

from .conftest import REPO_ROOT, eventually, remove_docker_container, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_docker_compose_runtime import generate_keypair

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def read_remote_file(context, relative: str) -> bytes:
    """Inspect private delivery output as the actual SSH operator."""
    path = context.remote.remote_root.rstrip("/") + "/" + relative
    result = subprocess.run(
        [
            *delivery.ssh_base_command(context.remote),
            "cat -- " + delivery.remote_path_expression(path),
        ],
        capture_output=True,
        timeout=15,
        check=True,
    )
    return result.stdout


@pytest.fixture(scope="module")
def watcher_image():
    supplied = os.environ.get("SFTPWARDEN_RELEASE_WATCHER_IMAGE")
    if supplied:
        run_external(["docker", "image", "inspect", supplied], timeout=30)
        yield supplied
        return
    image = "sftpwarden-watcher-gate:" + uuid4().hex[:12]
    try:
        run_external(
            ["docker", "build", "-t", image, "-f", "docker/watcher/Dockerfile", "."],
            cwd=REPO_ROOT,
            timeout=600,
        )
        yield image
    finally:
        result = run_external(["docker", "image", "rm", image], check=False, timeout=60)
        if result.returncode and "No such image" not in result.stderr:
            pytest.fail("Could not remove the owned watcher test image.")


@pytest.fixture
def ssh_delivery_project(tmp_path, monkeypatch, policy_image, cleanup_stack):
    return build_ssh_delivery_project(tmp_path, monkeypatch, policy_image, cleanup_stack)


def build_ssh_delivery_project(
    tmp_path, monkeypatch, policy_image, cleanup_stack, *, ssh_configurations=None
):
    tmp_path.mkdir(exist_ok=True)
    cleanup_stack.add(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    remote = tmp_path / ("remote project 'quoted " + uuid4().hex[:12])
    local = tmp_path / "local-project"
    remote.mkdir(mode=0o700)
    local.mkdir(mode=0o700)
    server = PolicyRuntime(remote, policy_image, sync=True)
    cleanup_stack.add(server.close)
    config = default_project_config("ssh-delivery")
    config.project.name = server.name
    config.docker.container_name = server.name
    config.docker.image = policy_image
    config.server.host = "127.0.0.1"
    config.auth.allow_password = False
    config.provider.path = f"{CONTAINER_PROVIDER_DATA_DIR}/users.yaml"
    config.healthcheck.interval_seconds = 1
    config.sync.interval_seconds = 5
    users = ProviderUsers(
        schema_version=2,
        users=[
            SFTPUser(
                username="alice", keys=[SFTPUserKey(name="alice", public_key=server.alice_public)]
            ),
            SFTPUser(
                username="control",
                keys=[SFTPUserKey(name="control", public_key=server.control_public)],
            ),
        ],
    )
    for root in (remote, local):
        write_config(root / "sftpwarden.yaml", config)
        provider_from_config(root, config).write(users)
    for name in ("data", "state", "host_keys"):
        (remote / name).mkdir(mode=0o700)
    compose = write_compose(config, remote, allow_local_build=False)
    # Let Docker reserve the port atomically instead of racing another process
    # between a host free-port probe and container creation.
    initial_compose = yaml.safe_load(compose.read_text())
    initial_compose["services"]["sftpwarden"]["ports"] = ["127.0.0.1:0:22"]
    compose.write_text(yaml.safe_dump(initial_compose))
    cleanup_stack.add(
        lambda: run_external(
            ["docker", "compose", "-f", str(compose), "down", "--volumes"], timeout=60, check=False
        )
    )
    run_external(
        ["docker", "compose", "-f", str(compose), "up", "-d", "--wait", "--wait-timeout", "90"],
        timeout=120,
    )
    server.refresh_port()
    config.server.port = server.port
    for root in (remote, local):
        write_config(root / "sftpwarden.yaml", config)
    write_compose(config, remote, allow_local_build=False)
    public = run_external(
        ["docker", "exec", server.name, "cat", "/etc/sftpwarden/host_keys/ssh_host_ed25519_key.pub"]
    ).stdout.strip()
    server.known_hosts.write_text(f"[127.0.0.1]:{server.port} {public}\n")
    assert server.connect("alice").returncode == 0

    # Resolve the engine's mount source rather than assuming Windows/macOS host
    # paths are identical to the Linux paths understood by a nested Docker client.
    mounts = json.loads(
        run_external(["docker", "inspect", "--format", "{{json .Mounts}}", server.name]).stdout
    )
    engine_root = str(
        PurePosixPath(
            next(
                item["Source"]
                for item in mounts
                if item["Destination"] == CONTAINER_PROVIDER_DATA_DIR
            )
        ).parent
    )
    gateway_image = f"sftpwarden-ssh-gate:{uuid4().hex[:12]}"
    build = tmp_path / "gateway-image"
    build.mkdir()
    base = f"FROM {policy_image}\n"
    if os.environ.get("SFTPWARDEN_RELEASE_BUILDER"):
        # An isolated BuildKit daemon cannot read Docker's host image store.
        # Export only this owned image into its context; no registry publication
        # or global builder cache is needed for the disposable SSH gateway.
        exported = f"sftpwarden-gateway-export-{uuid4().hex[:12]}"
        cleanup_stack.add(lambda: remove_docker_container(exported))
        run_external(["docker", "create", "--name", exported, policy_image])
        run_external(["docker", "export", "--output", str(build / "rootfs.tar"), exported])
        remove_docker_container(exported)
        base = "FROM scratch\nADD rootfs.tar /\n"
    (build / "Dockerfile").write_text(
        base + "RUN apk add --no-cache docker-cli docker-cli-compose openssh-client rsync\n"
    )
    cleanup_stack.add(
        lambda: run_external(["docker", "image", "rm", gateway_image], timeout=60, check=False)
    )
    image_platform = run_external(
        ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", policy_image]
    ).stdout.strip()
    run_external(
        ["docker", "build", "--platform", image_platform, "-t", gateway_image, str(build)],
        timeout=300,
    )
    key, public_key = generate_keypair(tmp_path / "deployment-key")
    credentials = tmp_path / "gateway-input"
    credentials.mkdir(mode=0o700)
    (credentials / "authorized_keys").write_text(public_key + "\n")
    gateway = f"sftpwarden-ssh-gate-{uuid4().hex[:12]}"
    cleanup_stack.add(lambda: remove_docker_container(gateway))
    run_external(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            gateway,
            "--publish",
            "127.0.0.1:0:22",
            "--volume",
            f"{tmp_path}:{PurePosixPath(engine_root).parent}",
            "--volume",
            f"{credentials}:/test-input:ro",
            "--volume",
            "/var/run/docker.sock:/var/run/docker.sock",
            "--entrypoint",
            "sh",
            gateway_image,
            "-ec",
            "mkdir -p /root/.ssh /run/sshd; "
            "cp /test-input/authorized_keys /root/.ssh/authorized_keys; "
            "chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys; passwd -d root; "
            "ssh-keygen -A; exec /usr/sbin/sshd -D -e -f /dev/null "
            "-o PermitRootLogin=prohibit-password -o PasswordAuthentication=no -o StrictModes=yes "
            "-o AuthorizedKeysFile=/root/.ssh/authorized_keys "
            "-o 'Subsystem=sftp internal-sftp'",
        ]
    )
    port = int(run_external(["docker", "port", gateway, "22/tcp"]).stdout.strip().rsplit(":", 1)[1])
    known = tmp_path / "gateway-known-hosts"
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host *\n  StrictHostKeyChecking yes\n  IdentitiesOnly yes\n"
        f'  UserKnownHostsFile "{known.as_posix()}"\n'
    )

    def gateway_ready():
        result = run_external(
            ["docker", "exec", gateway, "cat", "/etc/ssh/ssh_host_ed25519_key.pub"], check=False
        )
        if result.returncode:
            return False
        known.write_text(f"[127.0.0.1]:{port} {result.stdout.strip()}\n")
        return True

    eventually(gateway_ready, timeout_seconds=30, description="isolated deployment SSH identity")
    context = remote_context(
        name="ssh-delivery",
        provider=config.provider.type,
        remote_url=f"root@127.0.0.1:{engine_root}",
        local_root=local,
        remote_root=engine_root,
        remote_only=False,
        ssh_key=str(key),
        critical=False,
    )
    context.remote.port = port
    context.remote.compose_file = compose.name
    save_registry(ContextRegistry(default=context.name, contexts={context.name: context}))
    configurations = ssh_configurations if ssh_configurations is not None else {}
    configurations[str(key)] = ssh_config

    def isolated_ssh(endpoint, *, destination=True):
        command = ssh_base_command(endpoint, destination=destination)
        return [command[0], "-F", str(configurations[endpoint.ssh_key]), *command[1:]]

    monkeypatch.setattr(delivery, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(refresh_core, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(remote_environment, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(remote_locking, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(remote_deploy, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(remote_checks, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(deployment_upgrade, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(update_artifacts, "ssh_base_command", isolated_ssh)
    original_scp = remote_deploy.scp_upload_command

    def isolated_scp(endpoint, local_path, remote_path):
        command = original_scp(endpoint, local_path, remote_path)
        return [command[0], "-F", str(configurations[endpoint.ssh_key]), *command[1:]]

    monkeypatch.setattr(remote_deploy, "scp_upload_command", isolated_scp)
    monkeypatch.setattr(
        remote_deploy,
        "rsync_ssh_transport",
        lambda endpoint: shlex.join(isolated_ssh(endpoint, destination=False)),
    )
    monkeypatch.setattr(backup_remote, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(restore_remote, "ssh_base_command", isolated_ssh)
    monkeypatch.setattr(backup_remote, "remote_preparation_image", lambda: policy_image)
    monkeypatch.setattr(restore_remote, "remote_preparation_image", lambda: policy_image)
    eventually(
        lambda: (
            subprocess.run(
                [*isolated_ssh(context.remote), "true"], capture_output=True, timeout=15
            ).returncode
            == 0
        ),
        timeout_seconds=30,
        description="strict deployment SSH login",
    )
    server.gateway = gateway
    server.gateway_known_hosts = known
    return context, config, server, remote, local


def test_unavailable_ssh_target_does_not_acknowledge_or_block_an_independent_target(
    tmp_path,
    monkeypatch,
    policy_image,
    cleanup_stack,
):
    configurations = {}
    first = build_ssh_delivery_project(
        tmp_path / "first",
        monkeypatch,
        policy_image,
        cleanup_stack,
        ssh_configurations=configurations,
    )
    second = build_ssh_delivery_project(
        tmp_path / "second",
        monkeypatch,
        policy_image,
        cleanup_stack,
        ssh_configurations=configurations,
    )
    first[0].name = "unavailable"
    second[0].name = "healthy"
    save_registry(
        ContextRegistry(
            default="healthy", contexts={item[0].name: item[0] for item in (first, second)}
        )
    )
    poller = polling.WatchPoller()
    poller.poll()
    poller.poll()
    first_checkpoint = polling.checkpoint_path(
        first[0], provider_from_config(first[4], first[1]).path
    )
    previous = polling.load_checkpoint(first_checkpoint).applied
    run_external(["docker", "stop", "--time", "5", first[2].gateway], timeout=20)
    for _context, config, _server, _remote, local in (first, second):
        provider = provider_from_config(local, config)
        users = provider.read()
        users.users[0].disabled = True
        provider.write(users)
    poller.poll()
    poller.poll()
    assert polling.load_checkpoint(first_checkpoint).applied == previous
    assert polling.load_checkpoint(first_checkpoint).phase == "delivery_failed"
    second[2].assert_denied()
    assert first[2].connect("alice").returncode == 0
    run_external(["docker", "start", first[2].gateway])
    restarted = polling.WatchPoller()

    def recovered():
        restarted.poll()
        return polling.load_checkpoint(first_checkpoint).phase == "applied"

    eventually(
        recovered, timeout_seconds=90, description="unchanged provider retries after SSH recovery"
    )
    first[2].assert_denied()


def test_watcher_metadata_and_configuration_edits_have_only_their_declared_effects(
    ssh_delivery_project,
    monkeypatch,
):
    context, config, server, remote, local = ssh_delivery_project
    poller = polling.WatchPoller()
    poller.poll()
    poller.poll()
    original_config = (remote / "sftpwarden.yaml").read_bytes()
    runtime_file = "/etc/sftpwarden/authorized_keys/alice"
    before = run_external(
        ["docker", "exec", server.name, "stat", "-c", "%i:%Y", runtime_file]
    ).stdout
    monkeypatch.setattr(
        polling, "refresh_context", lambda *a, **k: pytest.fail("Metadata edit triggered refresh")
    )
    source = provider_from_config(local, config)
    users = source.read()
    users.users[0].comment = "Updated descriptive metadata"
    source.write(users)
    poller.poll()
    poller.poll()
    remote_users = yaml.safe_load(read_remote_file(context, "provider/users.yaml"))
    assert remote_users["users"][0]["comment"] == users.users[0].comment
    config.auth.allow_public_key = False
    config.auth.allow_password = True
    write_config(local / "sftpwarden.yaml", config)
    poller.poll()
    poller.poll()
    assert (remote / "sftpwarden.yaml").read_bytes() == original_config
    assert (
        run_external(["docker", "exec", server.name, "stat", "-c", "%i:%Y", runtime_file]).stdout
        == before
    )
    assert server.connect("alice").returncode == 0


def test_ssh_delivery_retries_after_refresh_failure_and_rejects_partial_input(
    ssh_delivery_project, monkeypatch
):
    context, config, server, remote, local = ssh_delivery_project
    source = provider_from_config(local, config)
    target = provider_from_config(remote, config)
    poller = polling.WatchPoller()
    poller.poll()
    poller.poll()
    checkpoint = polling.checkpoint_path(context, source.path)
    assert polling.load_checkpoint(checkpoint).phase == "applied"
    desired = source.read()
    desired.users[0].disabled = True
    source.write(desired)
    snapshot = delivery.read_snapshot(context, config)
    before = read_remote_file(context, "provider/users.yaml")
    destination = f"{context.remote.remote_root}/provider/users.yaml"
    command = [
        *delivery.ssh_base_command(context.remote),
        delivery.delivery_script(context, config, destination, snapshot),
    ]
    interrupted = subprocess.run(
        command,
        input=snapshot.content[: len(snapshot.content) // 2],
        capture_output=True,
        timeout=60,
    )
    assert interrupted.returncode != 0
    assert read_remote_file(context, "provider/users.yaml") == before
    assert not list(target.path.parent.glob(".sftpwarden-delivery.*"))

    real_refresh = polling.refresh_context

    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable refresh")

    monkeypatch.setattr(polling, "refresh_context", unavailable)
    poller.poll()
    poller.poll()
    failed = polling.load_checkpoint(checkpoint)
    assert failed.refresh_pending and failed.delivered == snapshot.digest
    assert failed.applied != snapshot.digest
    monkeypatch.setattr(polling, "refresh_context", real_refresh)
    restarted = polling.WatchPoller()
    restarted.poll()
    restarted.poll()
    assert polling.load_checkpoint(checkpoint).phase == "applied"
    server.assert_denied()
    assert server.connect("control").returncode == 0
    assert read_remote_file(context, "provider/users.yaml") == snapshot.content


def test_private_environment_delivery_over_strict_ssh(ssh_delivery_project):
    context, _config, _server, remote, local = ssh_delivery_project
    content = b"TEST_DSN='postgresql://user:synthetic$pass;@db/test'\n"
    (local / ".env").write_bytes(content)
    remote_environment.deliver_environment(context)
    assert read_remote_file(context, ".env") == content
    result = subprocess.run(
        [
            *delivery.ssh_base_command(context.remote),
            f"stat -c %a {delivery.remote_path_expression(context.remote.remote_root + '/.env')}",
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"600"
    assert not list(remote.glob(".sftpwarden-env.*"))


def test_docker_watcher_recreation_keeps_pending_delivery_and_private_key_modes(
    ssh_delivery_project,
    watcher_image,
    monkeypatch,
    cleanup_stack,
    tmp_path,
):
    context, config, server, remote, local = ssh_delivery_project
    network = "sftpwarden-watcher-" + uuid4().hex[:12]
    project = network

    def cleanup_network():
        disconnected = run_external(
            ["docker", "network", "disconnect", "--force", network, server.gateway], check=False
        )
        if (
            disconnected.returncode
            and "not connected" not in disconnected.stderr
            and "No such" not in disconnected.stderr
        ):
            pytest.fail("Owned gateway network could not be disconnected.")
        removed = run_external(["docker", "network", "rm", network], check=False)
        if removed.returncode and "not found" not in removed.stderr:
            pytest.fail("Owned watcher network could not be removed.")

    cleanup_stack.add(cleanup_network)
    run_external(["docker", "network", "create", network])
    run_external(["docker", "network", "connect", "--alias", "gateway", network, server.gateway])
    context.remote.host, context.remote.port = "gateway", 22
    save_registry(ContextRegistry(default=context.name, contexts={context.name: context}))
    known = tmp_path / "watcher-known-hosts"
    public = server.gateway_known_hosts.read_text().split(" ", 1)[1]
    known.write_text("gateway " + public)
    original_expand = docker_watcher.expand_path
    monkeypatch.setattr(
        docker_watcher,
        "expand_path",
        lambda value: known if str(value) == "~/.ssh/known_hosts" else original_expand(value),
    )
    artifact = docker_watcher.DockerWatcher.write(image=watcher_image)
    specification = yaml.safe_load(artifact.read_text())
    specification["networks"] = {"delivery": {"external": True, "name": network}}
    specification["services"]["sftpwarden-watcher"]["networks"] = ["delivery"]
    artifact.write_text(yaml.safe_dump(specification))
    command = ["docker", "compose", "--project-name", project, "-f", str(artifact)]
    cleanup_stack.add(lambda: run_external([*command, "down", "--timeout", "10"], timeout=60))
    run_external([*command, "up", "-d"], timeout=60)
    source = provider_from_config(local, config)
    checkpoint = polling.checkpoint_path(context, source.path)
    eventually(
        lambda: checkpoint.exists() and polling.load_checkpoint(checkpoint).phase == "applied",
        timeout_seconds=90,
        description="container watcher initial acknowledgement visible to host CLI",
    )
    identity = docker_watcher.docker_context_key_path(context)
    result = run_external(
        [*command, "exec", "-T", "sftpwarden-watcher", "stat", "-c", "%a", identity]
    )
    assert result.stdout.strip() == "600"
    run_external(["docker", "stop", "--time", "5", server.gateway], timeout=20)
    desired = source.read()
    desired.users[0].disabled = True
    source.write(desired)
    previous = polling.load_checkpoint(checkpoint).applied
    eventually(
        lambda: polling.load_checkpoint(checkpoint).phase == "delivery_failed",
        timeout_seconds=90,
        description="container watcher records an SSH outage",
    )
    assert polling.load_checkpoint(checkpoint).applied == previous
    run_external([*command, "up", "-d", "--force-recreate"], timeout=60)
    run_external(["docker", "start", server.gateway])
    eventually(
        lambda: (
            polling.load_checkpoint(checkpoint).phase == "applied"
            and polling.load_checkpoint(checkpoint).applied != previous
        ),
        timeout_seconds=90,
        description="pending delivery survives watcher recreation",
    )
    # This context has been rewritten to the watcher's Docker network endpoint.
    remote_users = run_external(
        [
            "docker",
            "exec",
            server.gateway,
            "cat",
            context.remote.remote_root + "/provider/users.yaml",
        ]
    ).stdout
    assert yaml.safe_load(remote_users)["users"][0]["disabled"]
    server.assert_denied()
    run_external([*command, "down", "--timeout", "10"], timeout=60)
    assert not run_external([*command, "ps", "--all", "--quiet"]).stdout.strip()
    # A native watcher must also be able to take over the same private lock.
    with polling.file_lock(checkpoint):
        assert polling.load_checkpoint(checkpoint).phase == "applied"


@pytest.mark.release_systemd
def test_native_systemd_watcher_lifecycle_and_recovery(
    ssh_delivery_project,
    tmp_path,
    monkeypatch,
    cleanup_stack,
):
    if os.environ.get("SFTPWARDEN_DISPOSABLE_SYSTEMD_RUNNER") != "1":
        pytest.fail("This gate requires an explicitly disposable native Linux systemd runner.")
    if not systemd_watcher.SystemdWatcher.is_supported():
        pytest.fail("The selected release runner has no active systemd manager.")
    context, config, server, _remote, local = ssh_delivery_project
    unit = "sftpwarden-release-" + uuid4().hex[:12] + ".service"
    destination = "/etc/systemd/system/" + unit
    wrapper = tmp_path / "bin"
    wrapper.mkdir(mode=0o700)
    ssh = wrapper / "ssh"
    ssh.write_text(
        "#!/bin/sh\nexec "
        + shlex.join([shutil.which("ssh"), "-F", str(tmp_path / "ssh_config")])
        + ' "$@"\n'
    )
    ssh.chmod(0o700)
    monkeypatch.setattr(
        systemd_watcher,
        "executable_command",
        lambda *a, **k: [sys.executable, "-I", "-m", "sftpwarden"],
    )
    text = systemd_watcher.render_systemd_unit()
    # Only this owned unit receives the private SSH configuration wrapper.
    text = text.replace(
        "[Service]\n",
        '[Service]\nEnvironment="PATH=' + str(wrapper) + ":" + os.environ["PATH"] + '"\n',
    )
    source = tmp_path / unit
    source.write_text(text)

    def remove_unit():
        result = run_external(["sudo", "systemctl", "stop", unit], check=False, timeout=30)
        if (
            result.returncode
            and "not loaded" not in result.stderr
            and "not found" not in result.stderr
        ):
            pytest.fail("Owned systemd watcher could not be stopped.")
        run_external(["sudo", "rm", "-f", destination])
        run_external(["sudo", "systemctl", "daemon-reload"])

    cleanup_stack.add(remove_unit)
    run_external(["sudo", "install", "-m", "0644", str(source), destination])
    run_external(["sudo", "systemctl", "daemon-reload"])
    run_external(["sudo", "systemctl", "start", unit])
    provider = provider_from_config(local, config)
    checkpoint = polling.checkpoint_path(context, provider.path)
    eventually(
        lambda: checkpoint.exists() and polling.load_checkpoint(checkpoint).phase == "applied",
        timeout_seconds=90,
        description="native systemd watcher delivery",
    )
    run_external(["docker", "stop", "--time", "5", server.gateway], timeout=20)
    users = provider.read()
    users.users[0].disabled = True
    provider.write(users)
    eventually(
        lambda: polling.load_checkpoint(checkpoint).phase == "delivery_failed",
        timeout_seconds=90,
        description="native watcher preserves pending SSH delivery",
    )
    run_external(["sudo", "systemctl", "restart", unit])
    run_external(["docker", "start", server.gateway])
    eventually(
        lambda: polling.load_checkpoint(checkpoint).phase == "applied",
        timeout_seconds=90,
        description="native watcher resumes pending delivery after restart",
    )
    server.assert_denied()
    run_external(["sudo", "systemctl", "stop", unit])
    assert run_external(["sudo", "systemctl", "is-active", unit], check=False).returncode != 0


@pytest.mark.parametrize("storage", ["local-sync", "remote-only"])
def test_remote_capture_and_fresh_recovery_preserve_sftp_identity_and_data(
    ssh_delivery_project,
    tmp_path,
    cleanup_stack,
    storage,
):
    from .test_fresh_recovery import transfer

    context, config, server, remote, local = ssh_delivery_project
    context.storage = storage
    if storage == "remote-only":
        context.root = context.config = ""
    else:
        # Local desired policy deliberately differs from the remote applied source.
        stale = provider_from_config(local, config).read()
        stale.users.clear()
        provider_from_config(local, config).write(stale)
    save_registry(ContextRegistry(default=context.name, contexts={context.name: context}))
    payload = tmp_path / "payload"
    payload.write_bytes(b"recovered through remote runtime mounts\n")
    batch = tmp_path / "transfer.batch"
    batch.write_text(
        f'put "{payload.as_posix()}" upload/recovery.txt\nchmod 600 upload/recovery.txt\n'
    )
    transfer(server, batch)
    archive = tmp_path / "remote-recovery.tgz"
    create_backup(
        context_name=context.name, output=str(archive), include_data=True, maintenance=True
    )
    assert server.connect("control").returncode == 0
    assert not (remote / ".sftpwarden-operation.lock").exists()

    recovered = tmp_path / "recovered project"
    cleanup_stack.add(
        lambda: run_external(
            [
                "docker",
                "compose",
                "-f",
                str(recovered / config.docker.compose_file),
                "down",
                "--volumes",
            ],
            timeout=60,
            check=False,
        )
    )
    run_external(
        ["docker", "compose", "-f", str(remote / config.docker.compose_file), "down"], timeout=60
    )
    provider_from_config(remote, config).path.unlink()
    context.remote.remote_root = str(
        PurePosixPath(context.remote.remote_root).parent / recovered.name
    )
    context.remote.remote_config = context.remote.remote_root + "/sftpwarden.yaml"
    save_registry(ContextRegistry(default=context.name, contexts={context.name: context}))
    result = restore_backup(context_name=context.name, backup_path=str(archive), include_data=True)
    assert result.target_state == "empty" and result.safety_backup is None
    assert not provider_from_config(remote, config).path.exists()
    command = (
        f"cd {delivery.remote_path_expression(context.remote.remote_root)} && "
        "docker compose up -d --wait --wait-timeout 90"
    )
    activated = subprocess.run(
        [*delivery.ssh_base_command(context.remote), command], capture_output=True, timeout=120
    )
    assert activated.returncode == 0
    server.refresh_port()
    assert server.connect("control").returncode == 0
    downloaded = tmp_path / "downloaded"
    batch.write_text(f'get upload/recovery.txt "{downloaded.as_posix()}"\n')
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    assert (
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "stat",
                "-c",
                "%u:%g:%a",
                "/etc/sftpwarden/host_keys/ssh_host_ed25519_key",
            ]
        ).stdout.strip()
        == "0:0:600"
    )
    assert not list(tmp_path.glob(".sftpwarden-operation.lock"))
