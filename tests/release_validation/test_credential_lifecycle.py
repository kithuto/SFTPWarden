"""Exercise mixed credentials and bounded shutdown against the candidate OpenSSH."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import yaml

from sftpwarden.security.passwords import hash_password

from .conftest import run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_docker_compose_runtime import generate_keypair

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_t01_t02_password_revocation_rotation_and_alternate_key(
    tmp_path, policy_image, cleanup_stack
):
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    cleanup_stack.add(server.close)
    replacement, replacement_public = generate_keypair(tmp_path / "replacement")
    config = server.inputs / "config.yaml"
    settings = yaml.safe_load(config.read_text())
    settings["auth"]["allow_password"] = True
    config.write_text(yaml.safe_dump(settings))
    password = "synthetic-credential-lifecycle-password"
    password_hash = hash_password(password)

    def publish(keys, selected_password):
        document = {
            "schema_version": 2,
            "users": [
                {"username": "alice", "keys": keys, "password_hash": selected_password},
                {
                    "username": "control",
                    "keys": [{"name": "control", "public_key": server.control_public}],
                },
            ],
        }
        temporary = server.inputs / "next.yaml"
        temporary.write_text(yaml.safe_dump(document))
        temporary.replace(server.inputs / "users.yaml")

    def refresh():
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
            timeout=35,
        )
        assert server.connect("control").returncode == 0

    original = {"name": "original", "public_key": server.alice_public}
    rotated = {"name": "replacement", "public_key": replacement_public}
    publish([original], password_hash)
    server.start()
    assert server.connect("alice").returncode == 0

    # Run the password client in Linux so native CLI host shell conventions do
    # not affect SSH_ASKPASS. All credentials and identities are synthetic.
    client_root = tmp_path / "password-client"
    client_root.mkdir(mode=0o700)
    (client_root / "askpass").write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_SFTP_PASSWORD"\n')
    (client_root / "askpass").chmod(0o700)
    trusted = server.known_hosts.read_text().split(" ", 1)[1]
    (client_root / "known_hosts").write_text("127.0.0.1 " + trusted)
    build = tmp_path / "client-build"
    build.mkdir()
    (build / "Dockerfile").write_text(
        f"FROM {policy_image}\nRUN apk add --no-cache openssh-client\n"
    )
    image = "sftpwarden-password-client:" + uuid4().hex[:12]
    cleanup_stack.add(lambda: run_external(["docker", "image", "rm", image], check=False))
    run_external(["docker", "build", "-t", image, str(build)], timeout=300)

    def password_connect():
        client = "sftpwarden-password-client-" + uuid4().hex[:12]
        cleanup_stack.add(
            lambda: run_external(["docker", "rm", "--force", "--volumes", client], check=False)
        )
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--name",
                client,
                "--network",
                "container:" + server.name,
                "--volume",
                f"{client_root}:/client:ro",
                "--env",
                "TEST_SFTP_PASSWORD",
                "--env",
                "SSH_ASKPASS=/client/askpass",
                "--env",
                "SSH_ASKPASS_REQUIRE=force",
                "--env",
                "DISPLAY=test",
                "--entrypoint",
                "sftp",
                image,
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=no",
                "-o",
                "PreferredAuthentications=password",
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "NumberOfPasswordPrompts=1",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "HostKeyAlgorithms=ssh-ed25519",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "UserKnownHostsFile=/client/known_hosts",
                "alice@127.0.0.1",
            ],
            env=dict(os.environ, TEST_SFTP_PASSWORD=password),
            input="pwd\nquit\n",
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    initial = password_connect()
    assert initial.returncode == 0, initial.stderr
    publish([], password_hash)
    refresh()
    assert server.connect("alice").returncode != 0
    assert password_connect().returncode == 0
    publish([], None)
    refresh()
    denied = password_connect()
    assert denied.returncode != 0 and "Permission denied" in denied.stderr
    assert server.connect("control").returncode == 0

    publish([rotated], password_hash)
    refresh()
    assert server.connect("alice").returncode != 0
    assert server.connect("alice", identity=replacement).returncode == 0
    assert password_connect().returncode == 0
    deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=12)
    publish([dict(original, expires_at=deadline.isoformat()), rotated], None)
    refresh()
    assert server.connect("alice").returncode == 0
    assert server.connect("alice", identity=replacement).returncode == 0
    while datetime.now(UTC) < deadline:
        time.sleep(0.05)
    assert server.connect("alice").returncode != 0
    assert server.connect("alice", identity=replacement).returncode == 0
    assert server.connect("control").returncode == 0


@pytest.mark.parametrize("stalled_worker", [False, True])
def test_t03_sigterm_reaps_required_children(tmp_path, policy_image, cleanup_stack, stalled_worker):
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    cleanup_stack.add(server.close)
    server.start()
    if stalled_worker:
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "python",
                "-c",
                "import json,os,signal; "
                "state=json.load(open('/var/lib/sftpwarden/lifecycle.json')); "
                "os.kill(state['worker']['pid'], signal.SIGSTOP)",
            ],
            timeout=10,
        )
    started = time.monotonic()
    run_external(["docker", "stop", "--time", "15", server.name], timeout=20)
    assert time.monotonic() - started < 15
    state = run_external(
        ["docker", "inspect", "--format", "{{.State.Running}}:{{.State.ExitCode}}", server.name]
    ).stdout.strip()
    assert state == "false:0", "Runtime required forced termination instead of orderly shutdown"
