"""Verified replacement of an installed watcher without deploying its servers."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version

from sftpwarden.config.global_config import load_global_config
from sftpwarden.system.commands import run_checked
from sftpwarden.utils._version import get_version
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.images import official_image
from sftpwarden.utils.paths import app_home, contexts_path, global_config_path
from sftpwarden.utils.persistence import atomic_bytes, reject_symlink
from sftpwarden.utils.state_transactions import state_transaction
from sftpwarden.watcher.base import WatcherInstallMode
from sftpwarden.watcher.process import process_path
from sftpwarden.watcher.registry import watcher_class

OFFICIAL_IMAGE = "ghcr.io/kithuto/sftpwarden-watcher"


def installation_receipt() -> Path:
    """Return the private watcher release receipt."""
    return app_home() / "watcher" / "installation.json"


def read_receipt() -> dict[str, Any]:
    """Read release evidence without treating corrupt content as a fresh install."""
    path = installation_receipt()
    reject_symlink(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid watcher receipt")
        return value
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        raise ContextError(
            "The watcher update receipt cannot be read; inspect it before retrying."
        ) from None


def process_is_current(receipt: dict[str, Any], target: str) -> bool:
    """Require recent evidence from the recorded process for a no-op update."""
    try:
        reject_symlink(process_path())
        process = json.loads(process_path().read_text(encoding="utf-8"))
        return bool(
            process["instance"] == receipt["process"]["instance"]
            and process["version"] == target
            and 0 <= time.time() - process["heartbeat"] < 10
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def watcher_upgrade_plan(target: str | None = None) -> dict[str, Any]:
    """Preserve backend/custom-image ownership and inactive installation intent."""
    state = load_global_config().watcher
    target = target or get_version()
    if not state.installed:
        return {"status": "not_installed", "changed": False}
    if state.managed_by != "sftpwarden" or not state.mode or not state.path:
        raise ContextError("Watcher ownership or installation metadata cannot be verified.")
    backend = watcher_class(WatcherInstallMode(state.mode))
    path = Path(state.path)
    if path.absolute() != backend.path().absolute():
        raise ContextError("The installed watcher path does not match its managed backend.")
    reject_symlink(path)
    image = None
    if state.mode == "docker":
        model = yaml.safe_load(path.read_text(encoding="utf-8"))
        image = model["services"]["sftpwarden-watcher"]["image"]
        if official_image(image, OFFICIAL_IMAGE):
            image = f"{OFFICIAL_IMAGE}:{target}"
        elif image != "sftpwarden-watcher:local":
            return {
                "status": "custom_image",
                "changed": False,
                "image": image,
                "action": "Custom image preserved; verify compatibility with its maintainer.",
            }
        else:
            image = None
    content = backend.render(image=image)
    account_patterns = {
        "systemd": r"(?m)^User=(.+)$",
        "supervisord": r"(?m)^user=(.+)$",
        "openrc": r"(?m)^command_user=(.+)$",
        "runit": r"(?m)^exec chpst -u (\S+)",
    }
    if state.mode in account_patterns and path.exists():
        pattern = account_patterns[state.mode]
        old_account = re.search(pattern, path.read_text(encoding="utf-8"))
        new_account = re.search(pattern, content)
        if (
            not old_account
            or not new_account
            or shlex.split(old_account[1]) != shlex.split(new_account[1])
        ):
            raise ContextError(
                "Watcher replacement would change its service account.",
                suggestion=(
                    "Run the update as its original owner or explicitly reinstall the service."
                ),
            )
    digest = hashlib.sha256(content.encode()).hexdigest()
    receipt = read_receipt()
    if receipt.get("version") and Version(receipt["version"]) > Version(target):
        raise ContextError("A newer watcher is installed; use its matching CLI.")
    changed = (
        receipt.get("status") != "complete"
        or receipt.get("version") != target
        or receipt.get("fingerprint") != digest
        or not path.exists()
        or path.read_text(encoding="utf-8") != content
        or (state.activated is not False and not process_is_current(receipt, target))
    )
    return {
        "status": "update_required" if changed else "current",
        "changed": changed,
        "previous": receipt.get("version"),
        "target": target,
        "mode": state.mode,
        "path": str(path),
        "image": image,
        "activated": state.activated is not False,
        "fingerprint": digest,
    }


def verify_process(target: str, started: float, previous: str | None) -> dict[str, Any]:
    """Wait for fresh evidence from the replacement, not an old installation record."""
    deadline = time.monotonic() + 75
    first = None
    while time.monotonic() < deadline:
        try:
            path = process_path()
            reject_symlink(path)
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                record["version"] == target
                and record["started"] >= started
                and record["instance"] != previous
                and 0 <= time.time() - record["heartbeat"] < 10
            ):
                if (
                    first
                    and record["heartbeat"] > first["heartbeat"]
                    and record["instance"] == first["instance"]
                ):
                    return dict(record)
                first = record
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(0.25)
    raise ContextError("The replacement watcher did not confirm its version and liveness.")


def _execute(commands: list[list[str]]) -> None:
    for command in commands:
        run_checked(
            command,
            timeout=180,
            error_type=ContextError,
            message="Watcher upgrade command failed.",
            fallback_suggestion="Inspect the scheduler and retry; checkpoints were retained.",
            private_output=True,
        )


def _stop_watcher(plan: dict[str, Any], pending: dict[str, Any]) -> None:
    """Retain each confirmed stop step so a failed replacement can continue."""
    if pending.get("stopped"):
        return
    backend = watcher_class(WatcherInstallMode(plan["mode"]))
    commands = backend.uninstall_commands(path=Path(plan["path"]))
    if plan["mode"] == "windows-task":
        commands.insert(0, ["schtasks", "/End", "/TN", "SFTPWarden Watcher"])
    for index, command in enumerate(commands):
        if index >= pending.get("stop_step", 0):
            _execute([command])
            pending["stop_step"] = index + 1
            atomic_bytes(installation_receipt(), json.dumps(pending).encode())
    pending["stopped"] = True
    atomic_bytes(installation_receipt(), json.dumps(pending).encode())


def pause_watcher() -> None:
    """Stop only the managed watcher before an explicitly coordinated rollout."""
    with state_transaction(contexts_path()), state_transaction(global_config_path()):
        state = load_global_config().watcher
        if not state.installed or state.activated is False:
            return
        plan = watcher_upgrade_plan()
        if plan["status"] == "custom_image":
            return
        previous = read_receipt()
        if previous.get("status") == "paused":
            return
        backend = watcher_class(WatcherInstallMode(plan["mode"]))
        if not backend.is_supported():
            raise ContextError("The installed watcher backend is unavailable.")
        backup = installation_receipt().with_name("previous-installation")
        if previous.get("status") == "complete" or not backup.exists():
            atomic_bytes(backup, Path(plan["path"]).read_bytes())
        pending = {**previous, "status": "pausing", "recovery": str(backup)}
        if previous.get("status") == "complete":
            pending.update(stopped=False, stop_step=0)
        _stop_watcher(plan, pending)
        pending["status"] = "paused"
        atomic_bytes(installation_receipt(), json.dumps(pending).encode())


def upgrade_watcher() -> dict[str, Any]:
    """Replace only the owned watcher; a failure never advances its release receipt."""
    with state_transaction(contexts_path()), state_transaction(global_config_path()):
        plan = watcher_upgrade_plan()
        if not plan["changed"]:
            return plan
        from sftpwarden.services.update_compatibility import assess_contexts, bundled_compatibility

        compatibility = assess_contexts(bundled_compatibility(), plan)
        if compatibility["requires_deploy_all"]:
            raise ContextError(
                "Watcher replacement requires deployment of incompatible contexts first.",
                suggestion="Run `sftpwarden upgrade` to review the required all-context rollout.",
            )
        backend = watcher_class(WatcherInstallMode(plan["mode"]))
        if plan["activated"] and not backend.is_supported():
            raise ContextError("The installed watcher backend is unavailable on this host.")
        path = Path(plan["path"])
        old_content = path.read_bytes()
        old_receipt = read_receipt()
        backup = installation_receipt().with_name("previous-installation")
        if old_receipt.get("status") not in {"pending", "paused"} or not backup.exists():
            atomic_bytes(backup, old_content)
        previous = (old_receipt.get("process") or {}).get("instance")
        pending = {
            **plan,
            "status": "pending",
            "recovery": str(backup),
            "stopped": old_receipt.get("status") == "paused" or old_receipt.get("stopped", False),
            "stop_step": old_receipt.get("stop_step", 0)
            if old_receipt.get("status") == "pending"
            else 0,
        }
        atomic_bytes(installation_receipt(), json.dumps(pending).encode())
        try:
            if plan["mode"] == "docker" and plan["activated"]:
                # Pull/build the candidate before replacing a working container.
                staged = path.with_name("upgrade-compose.yaml")
                atomic_bytes(staged, backend.render(image=plan["image"]).encode())
                try:
                    model = yaml.safe_load(staged.read_text(encoding="utf-8"))
                    service = model["services"]["sftpwarden-watcher"]
                    _execute(
                        [
                            [
                                "docker",
                                "compose",
                                "-f",
                                str(staged),
                                "build" if "build" in service else "pull",
                            ]
                        ]
                    )
                finally:
                    staged.unlink(missing_ok=True)
            elif plan["activated"] and not pending["stopped"]:
                _stop_watcher(plan, pending)
            backend.write(image=plan["image"])
            started = time.time()
            process = None
            if plan["activated"]:
                if plan["mode"] == "docker":
                    _execute(
                        [["docker", "compose", "-f", str(path), "up", "-d", "--force-recreate"]]
                    )
                else:
                    _execute(backend.commands())
                pending["stopped"] = False
                pending["stop_step"] = 0
                atomic_bytes(installation_receipt(), json.dumps(pending).encode())
                process = verify_process(get_version(), started, previous)
            result = {**plan, "status": "complete", "version": get_version(), "process": process}
            atomic_bytes(installation_receipt(), json.dumps(result).encode())
            return result
        except Exception:
            # Keep the actual incomplete state and exact previous file. Automatic
            # rollback cannot assume the old package still exists after self-update.
            atomic_bytes(installation_receipt(), json.dumps(pending).encode())
            raise
