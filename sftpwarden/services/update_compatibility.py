"""Pre-install compatibility decisions; coordinated deployment requires explicit consent."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from packaging.version import Version

from sftpwarden.config import FILE_PROVIDER_TYPES, load_config
from sftpwarden.config.global_config import load_global_config
from sftpwarden.contexts import ContextEntry, ContextType, load_registry
from sftpwarden.services.deployment_upgrade import plan_upgrade
from sftpwarden.services.releases import stable_version
from sftpwarden.utils.errors import SFTPWardenError


def validate_compatibility(value: Any, release: str) -> dict[str, Any]:
    """Accept declarative release requirements, never remote executable actions."""
    if not isinstance(value, dict) or value.get("format") != 1 or value.get("release") != release:
        raise ValueError("Invalid release compatibility metadata")
    for key in ("minimum_upgrade_runtime", "watcher_minimum_runtime"):
        if stable_version(value[key]) > stable_version(release):
            raise ValueError("Invalid runtime compatibility bound")
    if not isinstance(value.get("watcher_requires_provider_directory"), bool):
        raise ValueError("Invalid watcher path requirement")
    return {
        key: value[key]
        for key in (
            "format",
            "release",
            "minimum_upgrade_runtime",
            "watcher_minimum_runtime",
            "watcher_requires_provider_directory",
        )
    }


def bundled_compatibility() -> dict[str, Any]:
    """Read the compatibility contract shipped with this CLI."""
    from sftpwarden.utils._version import get_version

    value = json.loads(files("sftpwarden").joinpath("release_compatibility.json").read_text())
    return validate_compatibility(value, get_version())


def context_identity(entry: ContextEntry) -> str:
    """Bind consent to its context without publishing configuration or credentials."""
    digest = hashlib.sha256(entry.model_dump_json().encode())
    if entry.config:
        digest.update(Path(entry.config).read_bytes())
    elif entry.remote:
        from sftpwarden.services.deployment_upgrade import context_config

        digest.update(context_config(entry).model_dump_json().encode())
    return digest.hexdigest()


def validate_context_snapshot(contexts: list[dict[str, Any]]) -> None:
    """Refuse changed targets or settings before the first update mutation."""
    registry = load_registry()
    if set(registry.contexts) != {item["name"] for item in contexts}:
        raise SFTPWardenError("Context registry changed after confirmation; repeat upgrade.")
    for item in contexts:
        if context_identity(registry.contexts[item["name"]]) != item["identity"]:
            raise SFTPWardenError("A deployment target changed after confirmation; repeat upgrade.")


def assess_contexts(compatibility: dict[str, Any], watcher_plan: dict[str, Any]) -> dict[str, Any]:
    """Inspect every registered deployment before escalating upgrade scope."""
    registry = load_registry()
    state = load_global_config().watcher
    contexts = []
    incompatible = []
    for name, entry in sorted(registry.contexts.items()):
        plan = plan_upgrade(entry, target=compatibility["release"])
        watched = (
            state.installed
            and watcher_plan.get("changed")
            and entry.type == ContextType.REMOTE
            and entry.storage == "local-sync"
        )
        reason = None
        if plan.source and Version(plan.source) < Version(compatibility["minimum_upgrade_runtime"]):
            raise SFTPWardenError(f"Context {name} has no supported migration to this release.")
        if watched and plan.source:
            config = load_config(entry.config)
            if config.provider.type in FILE_PROVIDER_TYPES:
                if Version(plan.source) < Version(compatibility["watcher_minimum_runtime"]):
                    reason = "The new watcher requires a newer runtime delivery protocol."
                elif compatibility[
                    "watcher_requires_provider_directory"
                ] and not config.provider.path.startswith("/etc/sftpwarden/provider-data/"):
                    reason = "The watcher requires the provider directory migration."
        if reason and not plan.managed_image:
            raise SFTPWardenError(
                f"Context {name} uses an incompatible custom image.",
                suggestion="Prepare a compatible custom image first; it will not be replaced.",
            )
        if reason:
            incompatible.append({"context": name, "reason": reason, "version": plan.source})
        contexts.append(
            {"name": name, "identity": context_identity(entry), "deployed": plan.source}
        )
    return {
        "status": "incompatible" if incompatible else "deferred",
        "contexts": contexts,
        "incompatible": incompatible,
        "requires_deploy_all": bool(incompatible),
    }


def deploy_authorized_contexts(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use the new CLI's migrations only for the exact all-contexts consent snapshot."""
    from sftpwarden.services.deploy import apply_deployment_plan
    from sftpwarden.services.deployment_upgrade import begin_upgrade, complete_upgrade
    from sftpwarden.services.provider_schema import reconcile_provider_schema

    validate_context_snapshot(contexts)
    registry = load_registry()
    results = []
    for item in contexts:
        entry = registry.contexts[item["name"]]
        plan = plan_upgrade(entry)
        begin_upgrade(entry, plan)
        if entry.config:
            reconcile_provider_schema(entry, load_config(entry.config), backup=True)
        apply_deployment_plan(entry)
        results.append(
            {"context": entry.name, **complete_upgrade(entry, plan, reconcile_watcher=False)}
        )
    return results
