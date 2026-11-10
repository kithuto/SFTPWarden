"""T01/T02: fresh connections with strict identity and live positive controls."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from sftpwarden.config import ProviderConfig, ProviderType
from sftpwarden.providers import SQLiteProvider
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey

from .conftest import REPO_ROOT, eventually, run_external
from .test_docker_compose_runtime import generate_keypair

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


@pytest.fixture(scope="module")
def policy_image() -> Iterator[str]:
    supplied = os.environ.get("SFTPWARDEN_RELEASE_RUNTIME_IMAGE")
    if supplied:
        run_external(["docker", "image", "inspect", supplied], timeout=30)
        yield supplied
        return
    image = f"sftpwarden-policy:{uuid4().hex[:12]}"
    try:
        run_external(
            ["docker", "build", "-t", image, "-f", "docker/runtime/Dockerfile", "."],
            cwd=REPO_ROOT,
            timeout=900,
        )
        yield image
    finally:
        run_external(["docker", "image", "rm", image], timeout=60)


class PolicyRuntime:
    def __init__(self, root: Path, image: str, *, sync: bool) -> None:
        self.root = root
        self.image = image
        self.sync = sync
        self.name = f"sftpwarden-policy-{uuid4().hex[:12]}"
        self.port = 0
        self.alice, self.alice_public = generate_keypair(root / "alice")
        self.control, self.control_public = generate_keypair(root / "control")
        self.known_hosts = root / "known_hosts"
        self.inputs = root / "inputs"
        self.inputs.mkdir(mode=0o700)
        settings = {
            "version": 1,
            "project": {"name": "policy"},
            "provider": {"type": "yaml", "path": "/inputs/users.yaml", "user_schema": 2},
            "auth": {"allow_password": False, "allow_public_key": True},
            "sync": {"enabled": sync, "interval_seconds": 5},
        }
        (self.inputs / "config.yaml").write_text(yaml.safe_dump(settings))
        self.publish()

    def publish(
        self,
        *,
        revoked: bool = False,
        deleted: bool = False,
        disabled: bool = False,
        expires: datetime | None = None,
    ) -> None:
        key = {"name": "only", "public_key": self.alice_public, "disabled": revoked}
        if expires is not None:
            key["expires_at"] = expires.isoformat()
        users = {
            "schema_version": 2,
            "users": [
                {"username": "alice", "disabled": disabled, "keys": [] if deleted else [key]},
                {
                    "username": "control",
                    "keys": [{"name": "control", "public_key": self.control_public}],
                },
            ],
        }
        temporary = self.inputs / "next.yaml"
        temporary.write_text(yaml.safe_dump(users))
        temporary.replace(self.inputs / "users.yaml")

    def start(self, *, docker_args: tuple[str, ...] = ()) -> None:
        entrypoint = []
        command = []
        if not self.sync:
            # Independent OpenSSH: no worker or supervisor exists in this process tree.
            entrypoint = ["--entrypoint", "/bin/sh"]
            command = [
                "-ec",
                "\n".join(
                    [
                        "ssh-keygen -q -t ed25519 -N '' "
                        "-f /etc/sftpwarden/host_keys/ssh_host_ed25519_key",
                        "ssh-keygen -q -t rsa -b 3072 -N '' "
                        "-f /etc/sftpwarden/host_keys/ssh_host_rsa_key",
                        shlex.join(
                            [
                                "python",
                                "-c",
                                "from sftpwarden.runtime.configuration import "
                                "prepare_runtime_configuration; "
                                'prepare_runtime_configuration("/inputs/config.yaml")',
                            ]
                        ),
                        "sftpwarden runtime refresh --config /inputs/config.yaml",
                        "exec /usr/sbin/sshd -D -e",
                    ]
                ),
            ]
        run_external(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                self.name,
                "--publish",
                f"127.0.0.1:{self.port}:22",
                "--mount",
                f"type=bind,src={self.inputs},dst=/inputs,readonly",
                "--env",
                "SFTPWARDEN_CONFIG=/inputs/config.yaml",
                *docker_args,
                *entrypoint,
                self.image,
                *command,
            ]
        )
        self.refresh_port()

        def identity_ready() -> bool:
            result = run_external(
                [
                    "docker",
                    "exec",
                    self.name,
                    "cat",
                    "/etc/sftpwarden/host_keys/ssh_host_ed25519_key.pub",
                ],
                check=False,
                timeout=10,
            )
            if result.returncode:
                return False
            self.known_hosts.write_text(f"[127.0.0.1]:{self.port} {result.stdout.strip()}\n")
            return self.connect("control").returncode == 0

        eventually(
            identity_ready, timeout_seconds=60, description="strict positive-control authentication"
        )

    def refresh_port(self) -> None:
        """Resolve Docker's allocated port, retaining the previously pinned host key."""
        self.port = int(
            run_external(["docker", "port", self.name, "22/tcp"]).stdout.strip().rsplit(":", 1)[1]
        )
        if self.known_hosts.exists():
            trusted_key = self.known_hosts.read_text().split(" ", 1)[1]
            self.known_hosts.write_text(f"[127.0.0.1]:{self.port} {trusted_key}")

    def connect(
        self, username: str, *, identity: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        known_hosts_path = self.known_hosts.as_posix().replace("\\", "\\\\").replace('"', '\\"')
        return subprocess.run(
            [
                "sftp",
                "-F",
                os.devnull,
                "-b",
                "-",
                "-i",
                str(identity or self.root / username),
                "-P",
                str(self.port),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "IdentityAgent=none",
                "-o",
                "ControlMaster=no",
                "-o",
                "ControlPath=none",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "PreferredAuthentications=publickey",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "HostKeyAlgorithms=ssh-ed25519",
                "-o",
                f'UserKnownHostsFile="{known_hosts_path}"',
                "-o",
                "ConnectTimeout=5",
                f"{username}@127.0.0.1",
            ],
            input="pwd\n",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def assert_denied(self) -> None:
        assert self.connect("control").returncode == 0
        denied = self.connect("alice")
        assert denied.returncode != 0, "Revoked credential still authenticates"
        assert "Permission denied (publickey)" in denied.stderr, denied.stderr
        assert self.connect("control").returncode == 0

    def close(self) -> None:
        try:
            result = run_external(
                ["docker", "rm", "--force", "--volumes", self.name], timeout=60, check=False
            )
            if result.returncode and "No such container" not in result.stderr:
                pytest.fail(f"Could not remove owned test container {self.name}")
        finally:
            for name in ("alice", "control"):
                (self.root / name).unlink(missing_ok=True)
                (self.root / f"{name}.pub").unlink(missing_ok=True)


@pytest.mark.parametrize("change", ["revoked", "deleted", "disabled"])
def test_t01_last_credential_revoke_keeps_control_available(
    tmp_path: Path, policy_image: str, change: str
) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        server.start()
        assert server.connect("alice").returncode == 0
        server.publish(**{change: True})
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
        assert result.returncode == 0, result.stdout + result.stderr
        server.assert_denied()
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
        assert server.connect("control").returncode == 0
    finally:
        server.close()


@pytest.mark.parametrize("sync", [False, True], ids=["independent-openssh", "normal-worker"])
def test_t02_expiry_without_provider_edits(tmp_path: Path, policy_image: str, sync: bool) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=sync)
    try:
        server.start()
        deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=12)
        server.publish(expires=deadline)
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
        while datetime.now(UTC) < deadline:
            time.sleep(0.01)
        server.assert_denied()
    finally:
        server.close()


@pytest.mark.parametrize("schema", [1, 2])
def test_t10_t11_sqlite_runtime_preserves_other_credentials(
    tmp_path: Path,
    policy_image: str,
    schema: int,
) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        db_config = ProviderConfig(type=ProviderType.SQLITE, user_schema=schema)
        provider = SQLiteProvider(config=db_config, path=server.inputs / "users.sqlite")

        def user(name: str, public_key: str, key_name: str = "original") -> SFTPUser:
            if schema == 1:
                return SFTPUser(username=name, public_keys=[public_key])
            return SFTPUser(username=name, keys=[SFTPUserKey(name=key_name, public_key=public_key)])

        provider.write(
            ProviderUsers(
                schema_version=schema,
                users=[
                    user("alice", server.alice_public),
                    user("control", server.control_public),
                ],
            )
        )
        config_path = server.inputs / "config.yaml"
        settings = yaml.safe_load(config_path.read_text())
        settings["provider"] = {
            "type": "sqlite",
            "path": "/inputs/users.sqlite",
            "user_schema": schema,
        }
        config_path.write_text(yaml.safe_dump(settings))
        server.start()
        assert server.connect("alice").returncode == 0
        provider.upsert_user(user("alice", server.alice_public, "updated"))
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
        assert server.connect("control").returncode == 0
        assert server.connect("alice").returncode == 0
    finally:
        server.close()


def runtime_probe(server: PolicyRuntime, probe: str = "readiness") -> bool:
    result = run_external(
        [
            "docker",
            "exec",
            server.name,
            "sftpwarden",
            "runtime",
            "health",
            "--config",
            "/inputs/config.yaml",
            "--json",
            "--probe",
            probe,
        ],
        check=False,
        timeout=8,
    )
    return result.returncode == 0


@pytest.mark.parametrize("child", ["worker", "sshd"])
def test_t03_child_death_stops_the_runtime(tmp_path: Path, policy_image: str, child: str) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        server.start()
        eventually(
            lambda: runtime_probe(server), timeout_seconds=15, description="runtime readiness"
        )
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "python",
                "-c",
                "import json,os,signal; "
                "state=json.load(open('/var/lib/sftpwarden/lifecycle.json')); "
                f"os.kill(state[{child!r}]['pid'], signal.SIGKILL)",
            ],
            timeout=10,
        )

        def stopped() -> bool:
            result = run_external(
                ["docker", "inspect", server.name, "--format", "{{.State.Running}}"], timeout=5
            )
            return result.stdout.strip() == "false"

        eventually(
            stopped,
            timeout_seconds=12,
            interval_seconds=0.2,
            description="bounded child-failure shutdown",
        )
        assert not runtime_probe(server)
    finally:
        server.close()


@pytest.mark.parametrize("invalid_document", ["users: [invalid\n", "", "users:\n"])
def test_t04_invalid_provider_recovers_without_restart(
    tmp_path: Path, policy_image: str, invalid_document: str
) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        server.start()
        eventually(
            lambda: runtime_probe(server), timeout_seconds=15, description="runtime readiness"
        )
        state_before = run_external(
            ["docker", "exec", server.name, "cat", "/var/lib/sftpwarden/state.json"]
        ).stdout
        (server.inputs / "users.yaml").write_text(invalid_document)
        eventually(
            lambda: not runtime_probe(server),
            timeout_seconds=15,
            description="failed policy readiness",
        )
        assert runtime_probe(server, "liveness")
        assert server.connect("alice").returncode == 0
        state_after = run_external(
            ["docker", "exec", server.name, "cat", "/var/lib/sftpwarden/state.json"]
        ).stdout
        assert json.loads(state_before) == json.loads(state_after)
        server.publish(revoked=True)
        eventually(
            lambda: runtime_probe(server),
            timeout_seconds=35,
            description="provider recovery without restart",
        )
        server.assert_denied()
    finally:
        server.close()


def test_t06_nested_upload_symlink_preserves_outside_metadata(
    tmp_path: Path, policy_image: str
) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        server.start()
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "python",
                "-c",
                "import os,pathlib; p=pathlib.Path('/outside'); p.mkdir(mode=0o700); "
                "(p/'sentinel').write_text('unchanged'); os.chown(p,2222,2222)",
            ]
        )
        run_external(
            [
                "docker",
                "exec",
                "--user",
                "10000:10000",
                server.name,
                "python",
                "-c",
                "import os; os.symlink('/outside','/data/alice/upload/escape')",
            ]
        )
        source = server.inputs / "users.yaml"
        desired = yaml.safe_load(source.read_text())
        desired["users"][0]["upload_dir"] = "upload/escape"
        source.write_text(yaml.safe_dump(desired))
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
            timeout=35,
        )
        assert result.returncode != 0
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "python",
                "-c",
                "import pathlib; p=pathlib.Path('/outside'); s=p.stat(); "
                "assert (s.st_uid,s.st_gid,s.st_mode & 0o777)==(2222,2222,0o700); "
                "assert (p/'sentinel').read_text()=='unchanged'",
            ]
        )
        assert server.connect("control").returncode == 0
    finally:
        server.close()
