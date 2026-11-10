"""Management updates with an explicit all-context deployment consent boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

from packaging.version import Version

from sftpwarden.services.installation import installation_plan
from sftpwarden.services.releases import discover_release, stable_version
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import SFTPWardenError
from sftpwarden.utils.paths import app_home, contexts_path
from sftpwarden.utils.persistence import atomic_bytes, reject_symlink
from sftpwarden.utils.state_transactions import serialized_state

DEPLOY_REMINDER = (
    "SFTP servers have not been updated. Run `sftpwarden deploy --context <name>` "
    "for each deployment when you are ready. Deploy may interrupt active transfers; "
    "a verified backup is recommended."
)


def receipt_path() -> Path:
    """Return the private management update receipt."""
    return app_home() / "updates" / "management.json"


def upgrade_status() -> dict[str, Any]:
    """Read completion evidence without a release check or installation mutation."""
    path = receipt_path()
    reject_symlink(path)
    if not path.exists():
        return {"status": "not_started"}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("status"), str):
            raise ValueError("Invalid receipt")
    except (OSError, ValueError):
        raise SFTPWardenError(
            "The update receipt could not be read.",
            suggestion="Preserve the private receipt and inspect the installation before retrying.",
        ) from None
    if (
        record["status"] == "running"
        and record.get("worker_pid")
        and not worker_running(record["worker_pid"])
    ):
        record.update(
            status="incomplete",
            action="The update worker stopped. Inspect component receipts before retrying.",
        )
    return {
        "status": record["status"],
        **{
            key: record[key]
            for key in ("stage", "result", "action", "failure_type")
            if key in record
        },
    }


def worker_running(pid: int) -> bool:
    """Distinguish a live Windows handoff from an interrupted process receipt."""
    if os.name == "nt":
        import ctypes

        kernel = getattr(ctypes, "windll").kernel32  # noqa: B009
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 258)
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def upgrade_plan(target: str | None = None, *, check_only: bool = False) -> dict[str, Any]:
    """Discover a coordinated release before planning any installation changes."""
    from sftpwarden.services.update_compatibility import (
        assess_contexts,
        bundled_compatibility,
        validate_compatibility,
    )
    from sftpwarden.watcher.upgrade import watcher_upgrade_plan

    release = discover_release(fresh=True, target=target)
    if release["status"] != "available":
        raise SFTPWardenError("Release discovery is unavailable; the installation was not changed.")
    current = get_version()
    selected = release["version"]
    if Version(selected) < Version(current):
        if target is not None:
            raise SFTPWardenError("Automatic downgrades are not supported.")
        selected = current
    changed = Version(selected) > Version(current)
    plan: dict[str, Any] = {
        "cli": {"previous": current, "target": selected, "changed": changed},
        "watcher": {"status": "not_checked"} if check_only else watcher_upgrade_plan(selected),
        "deployments": {"status": "not_checked", "action": DEPLOY_REMINDER},
        "available": release["version"],
    }
    if changed and not check_only:
        plan["installation"] = installation_plan(selected).as_dict()
    if not check_only:
        compatibility = (
            bundled_compatibility()
            if selected == current
            else validate_compatibility(release.get("compatibility"), selected)
        )
        plan["deployments"] = {
            **assess_contexts(compatibility, plan["watcher"]),
            "action": DEPLOY_REMINDER,
        }
    return plan


@serialized_state(contexts_path)
def finish_management_update(
    target: str,
    *,
    deploy_contexts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Finish management work; deploy only an explicitly authorized context snapshot."""
    from sftpwarden.watcher.upgrade import upgrade_watcher

    if Version(get_version()) != stable_version(target):
        raise SFTPWardenError(
            "The new CLI version could not be verified; servers were not changed."
        )
    deployments: dict[str, Any] = {"status": "deferred", "action": DEPLOY_REMINDER}
    if deploy_contexts is not None:
        from sftpwarden.services.update_compatibility import deploy_authorized_contexts

        deployments = {"status": "complete", "results": deploy_authorized_contexts(deploy_contexts)}
    watcher = upgrade_watcher()
    if deploy_contexts is not None:
        from sftpwarden.contexts import load_registry
        from sftpwarden.services.deployment_upgrade import receipt_path as deployment_receipt

        registry = load_registry()
        for item in deployments["results"]:
            item["watcher"] = watcher
            if item["status"] == "runtime_ready":
                item["status"] = "complete"
            atomic_bytes(
                deployment_receipt(registry.contexts[item["context"]]), json.dumps(item).encode()
            )
    return {
        "cli": {"status": "current", "applied": get_version()},
        "watcher": watcher,
        "deployments": deployments,
    }


def apply_management_update(plan: dict[str, Any], *, json_output: bool) -> dict[str, Any]:
    """Install from a separate Python process and verify the complete result."""
    from sftpwarden.services.update_compatibility import validate_context_snapshot
    from sftpwarden.utils.persistence import file_lock

    path = receipt_path()
    with file_lock(path):
        if plan["cli"]["previous"] != get_version():
            raise SFTPWardenError("The CLI changed after planning; repeat upgrade.")
        if plan.get("deployments", {}).get("requires_deploy_all") and not plan.get(
            "deploy_all_authorized"
        ):
            raise SFTPWardenError("Incompatible deployments require explicit all-context consent.")
        if "contexts" in plan.get("deployments", {}):
            validate_context_snapshot(plan["deployments"]["contexts"])
        if path.exists():
            reject_symlink(path)
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("status") == "running" and worker_running(previous["worker_pid"]):
                raise SFTPWardenError(
                    "An update worker is already running; inspect its receipt first."
                )
        atomic_bytes(path, json.dumps({"status": "pending", "plan": plan}).encode())
        if not plan["cli"]["changed"]:
            if plan.get("deploy_all_authorized"):
                from sftpwarden.watcher.upgrade import pause_watcher

                pause_watcher()
            result = finish_management_update(
                plan["cli"]["target"],
                deploy_contexts=plan["deployments"]["contexts"]
                if plan.get("deploy_all_authorized")
                else None,
            )
        else:
            command = [sys.executable, "-I", "-m", "sftpwarden.services.upgrade_worker", str(path)]
            if os.name == "nt":
                # The Windows launcher may be locked until this command exits.
                bootstrap = (
                    "import sys; "
                    f"sys.path.insert(0, {sysconfig.get_path('purelib')!r}); "
                    "from sftpwarden.services.upgrade_worker import main; "
                    f"sys.argv=['worker', {str(path)!r}, '--parent', {str(os.getpid())!r}]; "
                    "raise SystemExit(main())"
                )
                # The base interpreter stays outside the tool environment which
                # uv may recreate. Only short-lived children load its native DLLs.
                worker = subprocess.Popen(
                    [getattr(sys, "_base_executable", sys.executable), "-I", "-c", bootstrap],
                    close_fds=True,
                )
                atomic_bytes(
                    path,
                    json.dumps(
                        {"status": "running", "plan": plan, "worker_pid": worker.pid}
                    ).encode(),
                )
                return {
                    "status": "running",
                    "receipt": str(path),
                    "message": (
                        "Update accepted. Check completion with `sftpwarden upgrade --status`."
                    ),
                }
            # The worker bounds each installer/deployment step. Killing only this
            # supervisor on a fixed aggregate deadline could orphan its installer.
            completed = subprocess.run(command, capture_output=True, check=False)
            reject_symlink(path)
            record = json.loads(path.read_text(encoding="utf-8"))
            if completed.returncode or record.get("status") != "complete":
                raise SFTPWardenError(
                    "The update is incomplete. Inspect component and deployment receipts.",
                    suggestion=f"Inspect {path} and retry with the owning installer available.",
                )
            result = record["result"]
        result["cli"].update(previous=plan["cli"]["previous"], target=plan["cli"]["target"])
        atomic_bytes(path, json.dumps({"status": "complete", "result": result}).encode())
        return result
