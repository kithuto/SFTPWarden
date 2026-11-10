"""Replace actual old watcher processes without touching deployment data."""

from __future__ import annotations

import json
import os
import subprocess
from uuid import uuid4

import pytest

from sftpwarden.config import ProviderType, write_config
from sftpwarden.config.global_config import load_global_config, save_global_config
from sftpwarden.utils._version import get_version
from sftpwarden.watcher import upgrade
from sftpwarden.watcher.backends.docker import DockerWatcher

from .conftest import (
    REPO_ROOT,
    docker_image_id,
    eventually,
    preserve_or_remove_image,
    remove_docker_container,
    run_external,
)
from .test_auth_policy import policy_image as policy_image
from .test_watcher_ssh import ssh_delivery_project as ssh_delivery_project
from .test_watcher_ssh import watcher_image as watcher_image

pytestmark = [
    pytest.mark.release_validation,
    pytest.mark.release_external,
    pytest.mark.release_docker,
]


@pytest.mark.release_upgrade
def test_real_docker_watcher_replacement_and_current_noop(
    tmp_path, monkeypatch, cleanup_stack, watcher_image, ssh_delivery_project
):
    context, config, server, remote, local = ssh_delivery_project
    # A database provider needs no file synchronization. Keep a real registered
    # deployment for CLI startup and prove this management update leaves it alone.
    config.provider.type = ProviderType.MYSQL
    config.provider.dsn = "mysql://synthetic:synthetic@unused.invalid/users"
    write_config(context.config, config)
    server_id = run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "watcher-upgrade-" + uuid4().hex[:10])
    old = "ghcr.io/kithuto/sftpwarden-watcher:1.3.0"
    new = f"ghcr.io/kithuto/sftpwarden-watcher:{get_version()}"
    old_id, new_id = docker_image_id(old), docker_image_id(new)
    cleanup_stack.add(lambda: preserve_or_remove_image(old, old_id))
    cleanup_stack.add(lambda: preserve_or_remove_image(new, new_id))
    if not old_id:
        run_external(["docker", "pull", "--platform", "linux/amd64", old], timeout=300)
    run_external(["docker", "tag", watcher_image, new])
    path = DockerWatcher.write(image=old)
    cleanup_stack.add(
        lambda: run_external(
            ["docker", "compose", "-f", str(path), "down", "--volumes"], timeout=60
        )
    )
    run_external(["docker", "compose", "-f", str(path), "up", "-d"], timeout=90)
    compose = ["docker", "compose", "-f", str(path)]
    previous = run_external([*compose, "ps", "-q", "sftpwarden-watcher"]).stdout.strip()
    assert (
        run_external(["docker", "exec", previous, "sftpwarden", "--version"])
        .stdout.strip()
        .endswith("1.3.0")
    )
    state = load_global_config()
    state.watcher.installed = True
    state.watcher.mode = "docker"
    state.watcher.path = str(path)
    state.watcher.activated = True
    save_global_config(state)
    checkpoint = upgrade.process_path().with_name("retained-delivery.json")
    checkpoint.write_text('{"applied":"retained"}')
    original = upgrade._execute

    def staged(commands):
        for command in commands:
            if command[-1] == "pull":
                # Publication is intentionally absent: the exact candidate was
                # loaded locally, so only its transport is replaced in this test.
                assert docker_image_id(new) == docker_image_id(watcher_image)
            else:
                original([command])

    monkeypatch.setattr(upgrade, "_execute", staged)
    sessions = []

    def close_sessions():
        for session in sessions:
            if session.poll() is None:
                session.kill()
            session.communicate(timeout=10)

    cleanup_stack.add(close_sessions)
    session = subprocess.Popen(
        server.connect("alice").args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sessions.append(session)
    session.stdin.write("mkdir upload/held-session\n")
    session.stdin.flush()
    eventually(
        lambda: (
            run_external(
                ["docker", "exec", server.name, "test", "-d", "/data/alice/upload/held-session"],
                check=False,
            ).returncode
            == 0
        ),
        timeout_seconds=15,
        description="SFTP session established before watcher replacement",
    )
    result = upgrade.upgrade_watcher()
    output, errors = session.communicate("rmdir upload/held-session\nquit\n", timeout=20)
    assert session.returncode == 0, output + errors
    assert result["status"] == "complete" and result["process"]["version"] == get_version()
    current = run_external([*compose, "ps", "-q", "sftpwarden-watcher"]).stdout.strip()
    assert current and current != previous
    assert run_external(["docker", "inspect", previous], check=False).returncode != 0
    assert checkpoint.read_text() == '{"applied":"retained"}'
    assert upgrade.upgrade_watcher()["status"] == "current"
    assert run_external([*compose, "ps", "-q", "sftpwarden-watcher"]).stdout.strip() == current
    assert (
        run_external(["docker", "inspect", "--format", "{{.Id}}", server.name]).stdout == server_id
    )
    assert server.connect("alice").returncode == 0


def test_real_native_supervisor_replacement(tmp_path, cleanup_stack):
    supplied = os.environ.get("SFTPWARDEN_RELEASE_NATIVE_WATCHER_IMAGE")
    image = supplied or f"sftpwarden-native-update:{uuid4().hex[:12]}"
    if not supplied:
        cleanup_stack.add(lambda: run_external(["docker", "image", "rm", image], timeout=60))
        base = (
            (REPO_ROOT / "docker/runtime/Dockerfile").read_text().splitlines()[0].split("=", 1)[1]
        )
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            f"FROM {base}\nRUN apk add --no-cache supervisor sudo && adduser -D sftpwarden\n"
            "RUN pip install --no-cache-dir sftpwarden==1.3.0\n"
            "COPY pyproject.toml README.md LICENSE /candidate/\n"
            "COPY sftpwarden /candidate/sftpwarden\n"
            "RUN python -m venv --system-site-packages /opt/candidate && "
            "/opt/candidate/bin/pip install --no-cache-dir /candidate && rm -rf /candidate\n"
        )
        run_external(
            ["docker", "build", "-t", image, "-f", str(dockerfile), "."], cwd=REPO_ROOT, timeout=300
        )
    name = "sftpwarden-native-update-" + uuid4().hex[:12]
    cleanup_stack.add(lambda: remove_docker_container(name))
    script = tmp_path / "native.py"
    script.write_text(NATIVE_SCRIPT)
    result = run_external(
        [
            "docker",
            "run",
            "--name",
            name,
            "--env",
            "SFTPWARDEN_HOME=/work/home",
            "--env",
            "SFTPWARDEN_NO_UPDATE_CHECK=1",
            "--env",
            "USER=root",
            "--volume",
            f"{script}:/test.py:ro",
            image,
            "python",
            "/test.py",
        ],
        timeout=120,
    )
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence["previous_pid"] != evidence["process"]["pid"]
    assert evidence["process"]["version"] == get_version()


NATIVE_SCRIPT = (
    r"""
import json, os, subprocess, time
from pathlib import Path
def run(*args):
    result=subprocess.run(args, text=True, capture_output=True)
    assert result.returncode==0, result.stdout+result.stderr
    return result.stdout.strip()
from sftpwarden.config import default_project_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
root=Path('/work/project')
root.mkdir(parents=True)
config=default_project_config('native-test')
write_config(root/'sftpwarden.yaml',config)
entry=local_context('native-test',root,config.provider.type)
save_registry(ContextRegistry(contexts={entry.name:entry}))
Path('/etc/supervisor/conf.d').mkdir(parents=True, exist_ok=True)
Path('/etc/supervisord.conf').write_text("""
    + repr("""[unix_http_server]
file=/run/supervisor.sock
[supervisord]
logfile=/tmp/supervisor.log
pidfile=/run/supervisord.pid
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix:///run/supervisor.sock
[include]
files=/etc/supervisor/conf.d/*.conf
""")
    + r""")
run('supervisord', '-c', '/etc/supervisord.conf')
assert run('sftpwarden', '--version').endswith('1.3.0')
run('sftpwarden', 'watcher', 'install', '--watcher', 'supervisord', '--no-activate')
home=Path(os.environ['SFTPWARDEN_HOME'])
source=home/'watcher/supervisord/sftpwarden-watch.conf'
assert source.exists(), list(home.rglob('*'))
for item in Path('/work').rglob('*'):
    item.chmod(0o755 if item.is_dir() else 0o644)
run('cp', str(source), '/etc/supervisor/conf.d/sftpwarden-watch.conf')
run('supervisorctl', 'reread')
run('supervisorctl', 'update')
deadline=time.monotonic()+15
while 'RUNNING' not in run('supervisorctl', 'status'):
    assert time.monotonic()<deadline
    time.sleep(.25)
previous=int(run('supervisorctl', 'pid', 'sftpwarden-watch'))
checkpoint=home/'watcher/checkpoints/retained.json'
checkpoint.parent.mkdir(parents=True, exist_ok=True)
checkpoint.write_text('{"applied":"retained"}')
code=("from sftpwarden.config.global_config import load_global_config,save_global_config; "
      "c=load_global_config(); c.watcher.activated=True; save_global_config(c); "
      "from sftpwarden.watcher.upgrade import upgrade_watcher; "
      "import json; print(json.dumps(upgrade_watcher()))")
completed=subprocess.run(['/opt/candidate/bin/python','-c',code],capture_output=True,text=True)
assert completed.returncode==0, completed.stdout+completed.stderr
record=json.loads(completed.stdout)
assert record['status']=='complete'
assert checkpoint.read_text()=='{"applied":"retained"}'
assert int(run('supervisorctl','pid','sftpwarden-watch'))==record['process']['pid']
print(json.dumps({'previous_pid':previous, 'process':record['process']}))
"""
)
