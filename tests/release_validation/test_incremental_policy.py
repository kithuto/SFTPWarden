"""Per-user effects and recovery after a partially applied, reverted provider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from .conftest import CleanupStack, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


def test_incremental_refresh_and_reverted_partial_failure(
    tmp_path: Path, policy_image: str, cleanup_stack: CleanupStack
) -> None:
    server = PolicyRuntime(tmp_path, policy_image, sync=False)
    cleanup_stack.add(server.close)
    server.start()
    refresh = [
        "docker",
        "exec",
        server.name,
        "python",
        "-c",
        "from sftpwarden.runtime.execution import execute_pass; "
        "print(execute_pass('/inputs/config.yaml'))",
    ]

    def control_stat() -> str:
        return run_external(
            [
                "docker",
                "exec",
                server.name,
                "python",
                "-c",
                "from pathlib import Path; "
                "s=Path('/etc/sftpwarden/authorized_keys/control').stat(); "
                "print(s.st_ino, s.st_mtime_ns)",
            ]
        ).stdout

    def state() -> dict:
        return json.loads(
            run_external(
                [
                    "docker",
                    "exec",
                    server.name,
                    "cat",
                    "/var/lib/sftpwarden/state.json",
                ]
            ).stdout
        )

    original_control = control_stat()
    server.publish(revoked=True)
    run_external(refresh)
    assert control_stat() == original_control
    server.assert_denied()
    server.publish()
    run_external(refresh)
    assert server.connect("alice").returncode == 0
    assert control_stat() == original_control
    original_state = state()

    # A real filesystem refusal occurs after Alice's revocation has been applied.
    run_external(["docker", "exec", server.name, "ln", "-s", "/data/alice", "/data/control/trap"])
    server.publish(revoked=True)
    path = server.inputs / "users.yaml"
    desired = yaml.safe_load(path.read_text())
    desired["users"][1]["upload_dir"] = "trap"
    path.write_text(yaml.safe_dump(desired))
    failed = run_external(refresh, check=False)
    assert failed.returncode != 0
    assert state()["fingerprint"] == original_state["fingerprint"]
    assert state()["pending"] is True
    server.assert_denied()

    # Reverting input must not make the old global fingerprint hide partial effects.
    server.publish()
    run_external(refresh)
    assert server.connect("alice").returncode == 0
    assert server.connect("control").returncode == 0
    assert state()["pending"] is False
    repaired_control = control_stat()
    run_external(refresh)
    assert control_stat() == repaired_control
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
        ]
    )
    assert control_stat() != repaired_control
    assert server.connect("alice").returncode == 0


@pytest.mark.parametrize("failed_command", ["newusers", "groupmod", "chpasswd"])
def test_interrupted_batch_keeps_new_accounts_locked_and_repairs_identity(
    tmp_path, policy_image, cleanup_stack, failed_command
):
    server = PolicyRuntime(tmp_path, policy_image, sync=False)
    cleanup_stack.add(server.close)
    server.start()
    path = server.inputs / "users.yaml"
    document = yaml.safe_load(path.read_text())
    for name in ("charlie", "dave"):
        document["users"].append(
            {"username": name, "keys": [{"name": "batch", "public_key": server.alice_public}]}
        )
    path.write_text(yaml.safe_dump(document))
    code = """
import grp, json
from pathlib import Path
from sftpwarden.runtime import core
original = core.run_checked
def checked(args, **kwargs):
    if args[0] == FAILED_COMMAND:
        if args[0] == 'newusers':
            kwargs['input_text'] = kwargs['input_text'].splitlines()[0] + '\\n'
            original(args, **kwargs)
        raise core.RuntimeError('Injected batch interruption')
    return original(args, **kwargs)
core.run_checked = checked
try:
    core.apply_once('/inputs/config.yaml')
except core.RuntimeError:
    pass
else:
    raise AssertionError('Failure was not exercised')
shadow = dict(line.split(':', 2)[:2] for line in Path('/etc/shadow').read_text().splitlines())
state = json.loads(Path('/var/lib/sftpwarden/state.json').read_text())
print(json.dumps({'pending': state['pending'],
                  'users': state['users'],
                  'created': [name for name in ('charlie', 'dave') if name in shadow],
                  'locked': all(shadow.get(name, '!') == '!' for name in ('charlie', 'dave')),
                  'control_member': 'control' in grp.getgrnam('sftpwarden_users').gr_mem}))
""".replace("FAILED_COMMAND", repr(failed_command))
    observed = json.loads(
        run_external(["docker", "exec", server.name, "python", "-c", code]).stdout
    )
    assert observed["pending"] and observed["locked"] and observed["control_member"]
    assert observed["created"] == (
        ["charlie"] if failed_command == "newusers" else ["charlie", "dave"]
    )
    assert server.connect("control").returncode == 0
    for name in ("charlie", "dave"):
        denied = server.connect(name, identity=server.alice)
        assert denied.returncode != 0 and "Permission denied" in denied.stderr
    run_external(
        [
            "docker",
            "exec",
            server.name,
            "python",
            "-c",
            "from sftpwarden.runtime.core import apply_once; apply_once('/inputs/config.yaml')",
        ]
    )
    state = json.loads(
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "cat",
                "/var/lib/sftpwarden/state.json",
            ]
        ).stdout
    )
    assert not state["pending"]
    for name in ("charlie", "dave"):
        assert state["users"][name]["uid"] == observed["users"][name]["uid"]
        assert server.connect(name, identity=server.alice).returncode == 0
