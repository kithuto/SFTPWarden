from pathlib import Path

import pytest
import yaml

from .conftest import run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def pause_worker(server: PolicyRuntime) -> None:
    run_external(
        [
            "docker",
            "exec",
            server.name,
            "python",
            "-c",
            "import json,os,signal; from pathlib import Path; "
            "from sftpwarden.runtime.lifecycle import runtime_lock; "
            "state=json.load(open('/var/lib/sftpwarden/lifecycle.json')); "
            "os.kill(state['worker']['pid'],signal.SIGSTOP)\n"
            "with runtime_lock(Path('/var/lib/sftpwarden')): pass",
        ],
        timeout=10,
    )


def test_t07_retired_identity_and_interrupted_create(tmp_path: Path, policy_image: str) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        server.start()
        pause_worker(server)
        old_uid = run_external(["docker", "exec", server.name, "id", "-u", "alice"]).stdout.strip()
        source = server.inputs / "users.yaml"
        desired = yaml.safe_load(source.read_text())
        desired["users"][0]["username"] = "bob"
        source.write_text(yaml.safe_dump(desired))
        # Inject a failure after real Linux account creation; the reservation must survive.
        result = run_external(
            [
                "docker",
                "exec",
                server.name,
                "python",
                "-c",
                "import sftpwarden.runtime.core as r\n"
                "def fail(config, resolved):\n"
                " if resolved.spec.username == 'bob': raise OSError('injected failure')\n"
                "r.ensure_directories=fail\n"
                "r.apply_once('/inputs/config.yaml',force=True)",
            ],
            check=False,
            timeout=35,
        )
        assert result.returncode != 0
        new_uid = run_external(["docker", "exec", server.name, "id", "-u", "bob"]).stdout.strip()
        assert new_uid != old_uid
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
        assert (
            run_external(["docker", "exec", server.name, "id", "-u", "bob"]).stdout.strip()
            == new_uid
        )
        assert server.connect("control").returncode == 0
    finally:
        server.close()


def test_t07_system_account_and_missing_identity_state_are_refused(
    tmp_path: Path, policy_image: str
) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    try:
        server.start()
        pause_worker(server)
        before = run_external(["docker", "exec", server.name, "getent", "passwd", "root"]).stdout
        source = server.inputs / "users.yaml"
        desired = yaml.safe_load(source.read_text())
        desired["users"][0]["username"] = "root"
        source.write_text(yaml.safe_dump(desired))
        command = [
            "docker",
            "exec",
            server.name,
            "sftpwarden",
            "runtime",
            "refresh",
            "--config",
            "/inputs/config.yaml",
        ]
        assert run_external(command, check=False, timeout=35).returncode != 0
        assert (
            run_external(["docker", "exec", server.name, "getent", "passwd", "root"]).stdout
            == before
        )
        server.publish()
        run_external(["docker", "exec", server.name, "rm", "/var/lib/sftpwarden/state.json"])
        assert run_external(command, check=False, timeout=35).returncode != 0
        assert server.connect("control").returncode == 0
    finally:
        server.close()
