"""Bounded reference workload with cleanup of every owned background resource."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time

import pytest

from sftpwarden.config import load_config, write_config
from sftpwarden.providers import provider_from_config
from sftpwarden.users import ProviderUsers, SFTPUser, SFTPUserKey, users_fingerprint

from .conftest import eventually, run_external
from .test_auth_policy import PolicyRuntime
from .test_auth_policy import policy_image as policy_image
from .test_docker_compose_runtime import generate_keypair

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]

INTERVAL = 10
CONVERGENCE_SECONDS = 2 * INTERVAL + 5
TRANSFER_COUNT = 20


async def concurrent_transfers(server, root):
    """Authenticate first, then run twenty overlapping rate-limited SFTP transfers."""
    processes = []
    payload = root / "load-payload.bin"
    payload.write_bytes(bytes(range(256)) * 1024)
    try:
        for _ in range(TRANSFER_COUNT):
            process = await asyncio.create_subprocess_exec(
                "sftp",
                "-F",
                os.devnull,
                "-b",
                "-",
                "-l",
                "1024",
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
                "StrictHostKeyChecking=yes",
                "-o",
                "HostKeyAlgorithms=ssh-ed25519",
                "-o",
                "ConnectTimeout=5",
                "-o",
                f'UserKnownHostsFile="{server.known_hosts.as_posix()}"',
                "alice@127.0.0.1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(process)
            process.stdin.write(b"pwd\n")
            await process.stdin.drain()

            async def authenticated(process=process):
                while line := await process.stdout.readline():
                    if b"Remote working directory:" in line:
                        return
                raise AssertionError("The reference transfer did not authenticate.")

            await asyncio.wait_for(authenticated(), timeout=10)
        assert all(process.returncode is None for process in processes)
        tasks = []
        for index, process in enumerate(processes):
            target = root / f"download-{index}.bin"
            commands = (
                f'put "{payload.as_posix()}" upload/load-{index}.bin\n'
                f'get upload/load-{index}.bin "{target.as_posix()}"\n'
            )
            tasks.append(process.communicate(commands.encode()))
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=60)
        assert all(process.returncode == 0 for process in processes)
        assert all(
            (root / f"download-{index}.bin").read_bytes() == payload.read_bytes()
            for index in range(TRANSFER_COUNT)
        )
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=10)


def test_reference_inventory_transfers_and_bounded_recovery(
    tmp_path,
    policy_image,
    cleanup_stack,
    record_property,
):
    server = PolicyRuntime(tmp_path, policy_image, sync=True)
    cleanup_stack.add(server.close)
    config = load_config(server.inputs / "config.yaml")
    config.sync.interval_seconds = INTERVAL
    write_config(server.inputs / "config.yaml", config)
    server.start()
    for path in (tmp_path / "third", tmp_path / "third.pub"):
        cleanup_stack.add(lambda path=path: path.unlink(missing_ok=True))
    _third_key, third_public = generate_keypair(tmp_path / "third")
    keys = [
        SFTPUserKey(name=f"key-{index}", public_key=value)
        for index, value in enumerate((server.alice_public, server.control_public, third_public))
    ]
    provider_config = config.model_copy(deep=True)
    provider_config.provider.path = str(server.inputs / "users.yaml")
    provider = provider_from_config(tmp_path, provider_config)

    def state():
        return json.loads(
            run_external(
                ["docker", "exec", server.name, "cat", "/var/lib/sftpwarden/state.json"]
            ).stdout
        )

    def reconcile(users):
        started = time.monotonic()
        provider.write(users)
        # Disabled users have no effective credentials even when their provider
        # record retains keys for a later explicit re-enable.
        effective = users.model_copy(deep=True)
        for user in effective.users:
            if user.disabled:
                user.keys = []
                user.public_keys = []
                user.password_hash = None
        fingerprint = users_fingerprint(effective)

        def converged():
            actual = state()
            return actual.get("fingerprint") == fingerprint and not actual.get("pending")

        try:
            eventually(
                converged,
                timeout_seconds=max(0, CONVERGENCE_SECONDS - (time.monotonic() - started)),
                interval_seconds=0.25,
                description=f"{len(users.users)}-user reference inventory convergence",
            )
        except pytest.fail.Exception:
            actual = state()
            status = json.loads(
                run_external(
                    ["docker", "exec", server.name, "cat", "/var/lib/sftpwarden/sync-status.json"]
                ).stdout
            )
            diagnostics = run_external(
                [
                    "docker",
                    "exec",
                    server.name,
                    "python",
                    "-c",
                    """
import json, os, pathlib, pwd, time, yaml
root = pathlib.Path('/var/lib/sftpwarden')
state = json.loads((root / 'state.json').read_text())
status = json.loads((root / 'sync-status.json').read_text())
accounts = {account.pw_name for account in pwd.getpwall()}
pid = status.get('attempt_pid')
children = pathlib.Path(f'/proc/{pid}/task/{pid}/children')
programs = []
if children.exists():
    for child in children.read_text().split():
        try:
            command = pathlib.Path(f'/proc/{child}/cmdline').read_bytes().split(b'\\0')[0]
            programs.append(pathlib.Path(os.fsdecode(command)).name)
        except FileNotFoundError:
            pass
print(json.dumps({
    'created_accounts': len(accounts.intersection(state['users'])),
    'attempt_seconds': round(time.monotonic() - status['attempt_monotonic'], 3),
    'child_programs': programs,
    'libyaml': yaml.__with_libyaml__,
    'cpu_count': os.cpu_count(),
}))
""",
                ],
                timeout=10,
            ).stdout.strip()
            pytest.fail(
                f"{len(users.users)} users did not converge within {CONVERGENCE_SECONDS}s; "
                f"reserved={len(actual['users'])}, pending={actual.get('pending')}, "
                f"failure={status.get('failure')}, elapsed={time.monotonic() - started:.2f}s; "
                f"runtime={diagnostics}"
            )
        elapsed = time.monotonic() - started
        assert elapsed <= CONVERGENCE_SECONDS
        return elapsed

    records = []
    for count in (100, 1000):
        users = ProviderUsers(
            schema_version=2,
            users=[
                SFTPUser(username="alice", keys=keys),
                SFTPUser(username="control", keys=keys),
                *[SFTPUser(username=f"load{index:04}", keys=keys) for index in range(count - 2)],
            ],
        )
        records.append({"users": count, "seconds": reconcile(users)})
    assert len(state()["users"]) == 1000
    asyncio.run(concurrent_transfers(server, tmp_path))
    before = run_external(
        [
            "docker",
            "exec",
            server.name,
            "stat",
            "-c",
            "%i:%Y",
            "/etc/passwd",
            "/etc/sftpwarden/authorized_keys/control",
        ]
    ).stdout
    refresh = [
        "docker",
        "exec",
        server.name,
        "python",
        "-c",
        (
            "from sftpwarden.runtime.execution import execute_pass; "
            "print(execute_pass('/inputs/config.yaml'))"
        ),
    ]
    # Public refresh forces reconciliation. The worker's no-op path must avoid writes.
    started = time.monotonic()
    run_external(refresh, timeout=40)
    no_op_seconds = time.monotonic() - started
    assert no_op_seconds < INTERVAL
    assert (
        run_external(
            [
                "docker",
                "exec",
                server.name,
                "stat",
                "-c",
                "%i:%Y",
                "/etc/passwd",
                "/etc/sftpwarden/authorized_keys/control",
            ]
        ).stdout
        == before
    )

    samples = []
    convergence = []
    metrics = """
import json, os, pathlib
rows = []
for path in pathlib.Path('/proc').iterdir():
    if not path.name.isdigit() or int(path.name) == os.getpid():
        continue
    try:
        command = (path / 'cmdline').read_bytes()
        supervisor = b'sftpwarden.runtime.supervisor' in command
        worker = b'sftpwarden.runtime.worker' in command and b'--once' not in command
        if not (supervisor or worker):
            continue
        lines = (path / 'status').read_text().splitlines()
        status = dict(line.split(':', 1) for line in lines if ':' in line)
        rows.append({'rss_kib': int(status.get('VmRSS', '0 kB').split()[0]),
                     'fds': len(list((path / 'fd').iterdir()))})
    except FileNotFoundError:
        pass
print(json.dumps({'processes': len(rows),
                  'rss_kib': sum(row['rss_kib'] for row in rows),
                  'fds': sum(row['fds'] for row in rows)}))
"""
    for cycle in range(4):
        samples.append(
            json.loads(
                run_external(["docker", "exec", server.name, "python", "-c", metrics]).stdout
            )
        )
        original = provider.path.read_bytes()
        provider.path.unlink()
        health = [
            "docker",
            "exec",
            server.name,
            "sftpwarden",
            "runtime",
            "health",
            "--config",
            "/inputs/config.yaml",
        ]
        eventually(
            lambda health=health: run_external(health, check=False).returncode != 0,
            timeout_seconds=CONVERGENCE_SECONDS,
            description="missing provider reports degraded health",
        )
        assert server.connect("control").returncode == 0
        provider.path.write_bytes(original)
        users.users[0].disabled = cycle % 2 == 0
        convergence.append(reconcile(users))
        if users.users[0].disabled:
            server.assert_denied()
        else:
            assert server.connect("alice").returncode == 0
        eventually(
            lambda health=health: run_external(health, check=False).returncode == 0,
            timeout_seconds=CONVERGENCE_SECONDS,
            description="health recovers without restarting the runtime",
        )
    assert max(convergence) <= CONVERGENCE_SECONDS  # Stronger than p95 for this bounded sample.
    assert samples[-1]["processes"] == samples[0]["processes"]
    assert samples[-1]["fds"] <= samples[0]["fds"]
    assert (
        statistics.median(item["rss_kib"] for item in samples[-2:]) <= 1.2 * samples[0]["rss_kib"]
    )
    resources = json.loads(
        run_external(["docker", "info", "--format", "{{json .}}"], timeout=30).stdout
    )
    record_property(
        "reference_environment",
        json.dumps(
            {
                "cpus": resources["NCPU"],
                "memory_bytes": resources["MemTotal"],
                "storage_driver": resources["Driver"],
                "network": "loopback-published Docker bridge",
                "inventory": records,
                "no_op_seconds": no_op_seconds,
                "recovery_seconds": convergence,
                "steady_processes": samples,
            },
            sort_keys=True,
        ),
    )
