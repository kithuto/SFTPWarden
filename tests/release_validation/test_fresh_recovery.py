"""Fresh Compose recovery with real private user data and pinned SSH identity."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from sftpwarden.config import ProviderType, default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.providers import provider_from_config
from sftpwarden.render.compose import write_compose
from sftpwarden.services import backup_runtime
from sftpwarden.services.backup import create_backup, restore_backup
from sftpwarden.users import ProviderUsers, SFTPUser
from sftpwarden.utils.constants import CONTAINER_PROVIDER_DATA_DIR
from sftpwarden.utils.errors import RuntimeError as SFTPWardenRuntimeError

from .conftest import REPO_ROOT, free_tcp_port, remove_docker_container, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def transfer(server: PolicyRuntime, batch: Path) -> None:
    run_external(
        [
            "sftp",
            "-F",
            os.devnull,
            "-b",
            str(batch),
            "-i",
            str(server.alice),
            "-P",
            str(server.port),
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
            "StrictHostKeyChecking=yes",
            "-o",
            "HostKeyAlgorithms=ssh-ed25519",
            "-o",
            f'UserKnownHostsFile="{server.known_hosts.as_posix()}"',
            "alice@127.0.0.1",
        ],
        timeout=30,
    )


@pytest.mark.parametrize(
    "provider_type",
    [ProviderType.YAML, ProviderType.CSV, ProviderType.SQLITE],
)
@pytest.mark.parametrize("destination", ["fresh", "existing"])
def test_fresh_restore_recovers_private_data_ownership_and_host_identity(
    tmp_path,
    monkeypatch,
    policy_image,
    cleanup_stack,
    provider_type,
    destination,
):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(backup_runtime, "remote_preparation_image", lambda: policy_image)
    root = tmp_path / "source"
    recovered = tmp_path / "recovered"
    client = tmp_path / "client"
    archive = tmp_path / "recovery.tar.gz"
    for directory in (root, recovered, client):
        cleanup_stack.add(lambda directory=directory: shutil.rmtree(directory, ignore_errors=True))
    cleanup_stack.add(lambda: archive.unlink(missing_ok=True))
    root.mkdir(mode=0o700)
    client.mkdir(mode=0o700)
    server = PolicyRuntime(client, policy_image, sync=True)
    cleanup_stack.add(server.close)
    config = default_project_config(server.name, provider_type)
    if destination == "existing":
        config.provider.path = f"{CONTAINER_PROVIDER_DATA_DIR}/{Path(config.provider.path).name}"
    config.docker.image = policy_image
    config.docker.container_name = server.name
    config.server.host = "127.0.0.1"
    config.server.port = free_tcp_port()
    config.healthcheck.interval_seconds = 1
    config.auth.allow_password = False
    write_config(root / "sftpwarden.yaml", config)
    provider = provider_from_config(root, config)
    provider.write(
        ProviderUsers(
            schema_version=2,
            users=[
                SFTPUser(username="alice", uid=2001, gid=2001, public_keys=[server.alice_public]),
                SFTPUser(
                    username="control", uid=2002, gid=2002, public_keys=[server.control_public]
                ),
            ],
        )
    )
    for name in ("host_keys", "state", "data"):
        (root / name).mkdir(mode=0o700)
    artifact = write_compose(config, root, allow_local_build=False)
    context = local_context("source", root, config.provider.type)
    save_registry(ContextRegistry(default="source", contexts={"source": context}))
    for path in (artifact, recovered / config.docker.compose_file):
        cleanup_stack.add(
            lambda path=path: run_external(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(path),
                    "down",
                    "--volumes",
                ],
                timeout=60,
                check=False,
            )
        )
    run_external(
        [
            "docker",
            "compose",
            "-f",
            str(artifact),
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
    payload = client / "payload.txt"
    payload.write_text("private recovery payload\n")
    batch = client / "transfer.batch"
    batch.write_text(
        f'put "{payload.as_posix()}" upload/payload.txt\nchmod 600 upload/payload.txt\n'
        f'put "{payload.as_posix()}" upload/closed.txt\nchmod 000 upload/closed.txt\n'
    )
    transfer(server, batch)
    remote_file = "/data/alice/upload/payload.txt"
    before = run_external(
        [
            "docker",
            "exec",
            "--user",
            "2001:2001",
            server.name,
            "stat",
            "-c",
            "%u:%g:%a",
            remote_file,
        ]
    ).stdout.strip()
    assert before == "2001:2001:600"
    # Cover both explicit downtime/resumption and a previously stopped target.
    running = provider_type == ProviderType.YAML
    if not running:
        run_external(["docker", "compose", "-f", str(artifact), "stop"], timeout=60)
    create_backup(context_name="source", output=str(archive), include_data=True, maintenance=True)
    assert (
        run_external(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}",
                server.name,
            ]
        ).stdout.strip()
        == str(running).lower()
    )
    if running:
        assert server.connect("control").returncode == 0
    if destination == "existing":
        # Change a file and retain unrelated project content before restoring the snapshot.
        (root / "notes.txt").write_text("keep this unrelated operator file")
        # The payload belongs to the SFTP account, including when the original
        # runtime is stopped. Mutate it through its actual Docker data mount.
        mutator = server.name + "-mutate"
        cleanup_stack.add(lambda: remove_docker_container(mutator))
        run_external(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                mutator,
                "--network",
                "none",
                "--volumes-from",
                server.name,
                "--user",
                "2001:2001",
                "--entrypoint",
                "python",
                policy_image,
                "-c",
                "from pathlib import Path; "
                "Path('/data/alice/upload/payload.txt').write_text('changed after capture')",
            ]
        )
        monkeypatch.chdir(tmp_path)
        if sys.platform == "linux" and not os.access(root / "host_keys", os.R_OK | os.X_OK):
            with pytest.raises(SFTPWardenRuntimeError, match="operator's permissions"):
                restore_backup(
                    context_name="source",
                    backup_path=str(archive),
                    include_data=True,
                    maintenance=True,
                )
            # This operation replaces protected host files. Exercise the explicit
            # administrator route only; the test and ordinary CLI remain unprivileged.
            bootstrap = (
                "import os,sys; os.environ['SFTPWARDEN_HOME']=sys.argv[1]; "
                "os.environ['TMPDIR']=sys.argv[3]; "
                "sys.path.insert(0,sys.argv[2]) if sys.argv[2] else None; "
                "from sftpwarden.services import backup_runtime; "
                "backup_runtime.remote_preparation_image=lambda image=sys.argv[4]: image; "
                "from sftpwarden.cli import app; "
                "sys.argv=['sftpwarden', *sys.argv[5:]]; app()"
            )
            restored = run_external(
                [
                    "sudo",
                    "-n",
                    sys.executable,
                    "-c",
                    bootstrap,
                    os.environ["SFTPWARDEN_HOME"],
                    ""
                    if os.environ.get("SFTPWARDEN_TEST_INSTALLED_ARTIFACT") == "1"
                    else str(REPO_ROOT),
                    tempfile.gettempdir(),
                    policy_image,
                    "restore",
                    str(archive),
                    "--context",
                    "source",
                    "--include-data",
                    "--maintenance",
                    "--yes",
                    "--json",
                ],
                timeout=180,
            )
            safety_backup = Path(json.loads(restored.stdout)["safety_backup"])
            assert (root / "sftpwarden.yaml").stat().st_uid == root.stat().st_uid
            assert provider.path.stat().st_uid == root.stat().st_uid
        else:
            result = restore_backup(
                context_name="source",
                backup_path=str(archive),
                include_data=True,
                maintenance=True,
            )
            safety_backup = result.safety_backup
        assert safety_backup.is_file()
        cleanup_stack.add(lambda: safety_backup.unlink(missing_ok=True))
        assert (root / "notes.txt").read_text() == "keep this unrelated operator file"
        journal = json.loads((root / ".sftpwarden" / "restore-transaction.json").read_text())
        assert journal["phase"] == "files_restored"
        assert not journal["pending"]
        assert (
            run_external(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    server.name,
                ]
            ).stdout.strip()
            == "false"
        )
        recovered = root
    else:
        if running:
            run_external(["docker", "compose", "-f", str(artifact), "stop"], timeout=60)
        provider.path.unlink()
        result = restore_backup(
            context_name=None,
            backup_path=str(archive),
            target_root=str(recovered),
            include_data=True,
        )
        assert result.safety_backup is None
        assert not provider.path.exists()
    # Activation is a separate explicit operator action.
    run_external(["docker", "compose", "-f", str(artifact), "down"], timeout=60)
    run_external(
        [
            "docker",
            "compose",
            "-f",
            str(recovered / config.docker.compose_file),
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "90",
        ],
        timeout=120,
    )
    server.refresh_port()
    assert server.connect("control").returncode == 0
    assert (
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "stat",
                "-c",
                "%u:%g:%a",
                remote_file,
            ]
        ).stdout.strip()
        == before
    )
    assert (
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "stat",
                "-c",
                "%u:%g:%a",
                "/data/alice/upload/closed.txt",
            ]
        ).stdout.strip()
        == "2001:2001:0"
    )
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
    downloaded = client / "downloaded.txt"
    batch.write_text(f'get upload/payload.txt "{downloaded.as_posix()}"\n')
    transfer(server, batch)
    assert downloaded.read_bytes() == payload.read_bytes()
    batch.write_text(f'put "{payload.as_posix()}" upload/after-recovery.txt\n')
    transfer(server, batch)
