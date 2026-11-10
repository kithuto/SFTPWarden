"""Installer handoff that retains loaded code until a fresh candidate process verifies it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from sftpwarden.utils.persistence import atomic_bytes, reject_symlink


def record_failure(path: Path, error: Exception | None = None) -> None:
    """Retain the consent and original plan without storing private installer output."""
    try:
        reject_symlink(path)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    record.update(
        status="incomplete",
        failure_type=type(error).__name__ if error else "unknown",
        action=(
            "Inspect component/deployment receipts; repeat upgrade to finish. "
            "The watcher may be paused."
        ),
    )
    atomic_bytes(path, json.dumps(record).encode())


def wait_parent(pid: int) -> None:
    """Let Windows release the original console launcher before pip replaces it."""
    import ctypes

    kernel = getattr(ctypes, "windll").kernel32  # noqa: B009 - absent from POSIX stubs
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            if kernel.WaitForSingleObject(ctypes.c_void_p(handle), 60000) != 0:
                raise RuntimeError("Original CLI process did not exit")
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))


def perform(path: Path) -> None:
    """Apply the pinned installer plan and verify it in the updated environment."""
    reject_symlink(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    plan = record["plan"]
    installation = plan["installation"]
    target = plan["cli"]["target"]
    contexts = plan["deployments"]["contexts"] if plan.get("deploy_all_authorized") else None
    if plan.get("deployments", {}).get("requires_deploy_all") and contexts is None:
        raise RuntimeError("All-context deployment was not authorized")
    if "contexts" in plan.get("deployments", {}):
        old_cli_step(
            installation,
            "from sftpwarden.services.update_compatibility import "
            "validate_context_snapshot; "
            f"validate_context_snapshot({plan['deployments']['contexts']!r})",
        )

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    record["stage"] = "preparing_artifacts"
    atomic_bytes(path, json.dumps(record).encode())
    with tempfile.TemporaryDirectory(prefix="sftpwarden-upgrade-") as temporary:
        constraint = Path(temporary) / "release.txt"
        constraint.write_text(f"sftpwarden=={target}\n", encoding="utf-8")
        for name in ("PIP_CONSTRAINT", "UV_CONSTRAINT"):
            environment[name] = " ".join(filter(None, [environment.get(name), constraint.as_uri()]))
        from sftpwarden.services.installation import installer_command, prepare_installation

        prepare_installation(installation, target, Path(temporary), environment)
        record["stage"] = "artifacts_prepared"
        atomic_bytes(path, json.dumps(record).encode())
        old_cli_step(
            installation, "from sftpwarden.watcher.upgrade import pause_watcher; pause_watcher()"
        )
        record["stage"] = "installing_cli"
        atomic_bytes(path, json.dumps(record).encode())
        completed = subprocess.run(
            installer_command(installation, target, Path(temporary), environment),
            env=environment,
            capture_output=True,
            timeout=600,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                "Package installation failed; use the owning package manager to recover"
            )
        record["stage"] = "verifying_components"
        atomic_bytes(path, json.dumps(record).encode())
        probe = (
            "import json,sys,os,sysconfig,subprocess,importlib.metadata as m; "
            "from pathlib import Path; "
            "import sftpwarden; "
            f"assert Path(sys.prefix).resolve()==Path({installation['prefix']!r}).resolve(); "
            "package=Path(sftpwarden.__file__).resolve(); "
            "assert package.is_relative_to(Path(sys.prefix).resolve()); "
            f"assert m.version('sftpwarden')=={target!r}; "
            f"[m.version(n) for n in {installation['drivers']!r}]; "
            "launcher=Path(sysconfig.get_path('scripts'))/"
            "('sftpwarden.exe' if os.name=='nt' else 'sftpwarden'); "
            "cli=subprocess.run([str(launcher),'--version'],"
            "capture_output=True,text=True,timeout=20); "
            f"assert cli.returncode==0 and {target!r} in cli.stdout; "
            "from sftpwarden.services.upgrade import finish_management_update; "
            f"print(json.dumps(finish_management_update({target!r}, "
            f"deploy_contexts={contexts!r})))"
        )
        verified = subprocess.run(
            [installation["python"], "-I", "-c", probe],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600 + 180 * len(contexts or []),
            check=False,
        )
        if verified.returncode:
            raise RuntimeError("CLI or watcher verification failed; repeat upgrade to finish")
        result = json.loads(verified.stdout)
        result["cli"].update(previous=plan["cli"].get("previous"), target=target)
        atomic_bytes(
            path, json.dumps({"status": "complete", "plan": plan, "result": result}).encode()
        )


def old_cli_step(installation: dict[str, Any], code: str) -> None:
    """Exit the old environment before replacement, releasing native Windows DLLs."""
    result = subprocess.run(
        [installation["python"], "-I", "-c", code], capture_output=True, timeout=180, check=False
    )
    if result.returncode:
        raise RuntimeError("Pre-install verification or watcher pause failed; inspect the receipts")


def main() -> int:
    """Execute the private handoff while retaining honest failure evidence."""
    path = Path(sys.argv[1])
    try:
        if len(sys.argv) == 4 and sys.argv[2] == "--parent":
            wait_parent(int(sys.argv[3]))
            from sftpwarden.utils.persistence import file_lock

            with file_lock(path):
                perform(path)
        else:
            perform(path)
    except Exception as error:  # noqa: BLE001
        # Do not retain installer output, which may contain private index credentials.
        record_failure(path, error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
