"""Real installer handoffs in disposable tool environments; no public publication."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.version import Version

from sftpwarden.utils._version import get_version

from .conftest import REPO_ROOT

pytestmark = [pytest.mark.release_validation, pytest.mark.release_external]


def execute(command, *, env, cwd, timeout=180):
    """Keep installer diagnostics private even when a configured index fails."""
    result = subprocess.run(
        command, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    assert result.returncode == 0, f"Isolated installer operation failed: {command[0]}"
    return result.stdout


@pytest.fixture(scope="module")
def installer_artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("management updates ")
    try:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PIP_", "PIPX_", "UV_", "PYTHONPATH", "SFTPWARDEN_"))
        }
        env.update(
            PIP_CONFIG_FILE=os.devnull,
            PIP_INDEX_URL="https://pypi.org/simple",
            UV_NO_CONFIG="1",
            UV_DEFAULT_INDEX="https://pypi.org/simple",
            PIP_DISABLE_PIP_VERSION_CHECK="1",
            SFTPWARDEN_NO_UPDATE_CHECK="1",
        )
        managers = root / "managers"
        execute([sys.executable, "-m", "venv", str(managers)], env=env, cwd=root)
        scripts = "Scripts" if os.name == "nt" else "bin"
        python = managers / scripts / ("python.exe" if os.name == "nt" else "python")
        execute([str(python), "-m", "pip", "install", "pipx", "uv", "build"], env=env, cwd=root)
        source = root / "source"
        source.mkdir()
        for filename in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copyfile(REPO_ROOT / filename, source / filename)
        shutil.copytree(
            REPO_ROOT / "sftpwarden",
            source / "sftpwarden",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        wheels = root / "wheels"
        current = get_version()
        parsed = Version(current)
        future = f"{parsed.major}.{parsed.minor}.{parsed.micro + 1}"
        execute(
            [str(python), "-m", "build", "--wheel", "--outdir", str(wheels)], env=env, cwd=source
        )
        project = source / "pyproject.toml"
        project.write_text(
            project.read_text().replace(f'version = "{current}"', f'version = "{future}"', 1)
        )
        compatibility_file = source / "sftpwarden" / "release_compatibility.json"
        compatibility = json.loads(compatibility_file.read_text())
        compatibility["release"] = future
        compatibility_file.write_text(json.dumps(compatibility))
        execute(
            [str(python), "-m", "build", "--wheel", "--outdir", str(wheels)], env=env, cwd=source
        )
        env["PATH"] = str(managers / scripts) + os.pathsep + env["PATH"]
        env["PIP_FIND_LINKS"] = wheels.as_uri()
        env["UV_FIND_LINKS"] = wheels.as_uri()
        yield root, env, wheels, current, future, compatibility
    finally:
        shutil.rmtree(root, ignore_errors=False)


@pytest.mark.parametrize("manager", ["pip", "pipx", "uv"])
def test_real_cli_upgrade_preserves_owner_and_provider_extra(installer_artifacts, manager):
    root, base_env, wheels, current, future, compatibility = installer_artifacts
    work = root / manager
    work.mkdir()
    env = {**base_env, "SFTPWARDEN_HOME": str(work / "home")}
    scripts = "Scripts" if os.name == "nt" else "bin"
    binary = "python.exe" if os.name == "nt" else "python"
    requirement = f"sftpwarden[mysql]=={current}"
    if manager == "pip":
        prefix = work / "venv"
        execute([sys.executable, "-m", "venv", str(prefix)], env=env, cwd=work)
        execute(
            [str(prefix / scripts / binary), "-m", "pip", "install", requirement], env=env, cwd=work
        )
    elif manager == "pipx":
        env.update(
            PIPX_HOME=str(work / "pipx"),
            PIPX_BIN_DIR=str(work / "bin"),
            PIPX_MAN_DIR=str(work / "man"),
        )
        prefix = work / "pipx" / "venvs" / "sftpwarden"
        execute(
            [
                "pipx",
                "install",
                requirement,
                "--python",
                sys.executable,
                "--pip-args",
                shlex.join(["--find-links", str(wheels)]),
            ],
            env=env,
            cwd=work,
        )
    else:
        env.update(UV_TOOL_DIR=str(work / "tools"), UV_TOOL_BIN_DIR=str(work / "bin"))
        prefix = work / "tools" / "sftpwarden"
        execute(
            ["uv", "tool", "install", requirement, "--python", sys.executable], env=env, cwd=work
        )
    python = str(prefix / scripts / binary)
    # Only release discovery is synthetic: the two fixture wheels are private,
    # and all package replacement, ownership checks and fresh verification are real.
    release = {"status": "available", "version": future, "compatibility": compatibility}
    invocation = (
        "import sys; from sftpwarden.services import releases; "
        f"releases.discover_release=lambda **kw: {release!r}; "
        "from sftpwarden.cli import app; sys.argv=['sftpwarden','upgrade','--yes','--json']; app()"
    )
    completed = subprocess.run(
        [python, "-I", "-c", invocation],
        env=env,
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    receipt_path = work / "home" / "updates" / "management.json"
    evidence = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    assert completed.returncode == 0, {
        "stage": evidence.get("stage"),
        "failure_type": evidence.get("failure_type"),
        "result": completed.stdout,
    }
    output = completed.stdout
    result = json.loads(output)
    if os.name == "nt":
        # Completion is asynchronous on Windows so the original .exe can exit.
        import time

        receipt = Path(result["receipt"])
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            record = json.loads(receipt.read_text())
            if record["status"] in {"complete", "incomplete"}:
                assert record["status"] == "complete"
                result = record["result"]
                break
            time.sleep(0.5)
    assert result["cli"]["applied"] == future
    assert result["watcher"]["status"] == "not_installed"
    assert result["deployments"]["status"] == "deferred"
    assert "SFTP servers have not been updated" in result["deployments"]["action"]
    verify = (
        "import json,sys,importlib.metadata as m; "
        "print(json.dumps({'prefix':sys.prefix,'version':m.version('sftpwarden'),'mysql':m.version('PyMySQL')}))"
    )
    applied = json.loads(execute([python, "-I", "-c", verify], env=env, cwd=work))
    assert Path(applied["prefix"]).resolve() == prefix.resolve()
    assert applied["version"] == future and applied["mysql"]
    assert not (work / "home" / "updates" / "deployments").exists()


def test_advisory_check_has_real_timeout_and_cached_offline_result(
    tmp_path, monkeypatch, cleanup_stack
):
    """A silent local HTTPS proxy must not leave the CLI waiting on network I/O."""
    import socketserver
    import threading
    import time

    from sftpwarden.services.releases import discover_release

    cleanup_stack.add(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    stop = threading.Event()

    class SilentProxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(2048)
            stop.wait(5)

    server = socketserver.TCPServer(("127.0.0.1", 0), SilentProxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        proxy = f"http://127.0.0.1:{server.server_address[1]}"
        monkeypatch.setenv("HTTPS_PROXY", proxy)
        monkeypatch.setenv("https_proxy", proxy)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        start = time.monotonic()
        assert discover_release()["status"] == "unavailable"
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
        start = time.monotonic()
        assert discover_release()["status"] == "unavailable"
        cached = time.monotonic() - start
        assert cached < 0.25
        print(f"advisory_timeout_seconds={elapsed:.3f}; cache_hit_seconds={cached:.4f}")
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
