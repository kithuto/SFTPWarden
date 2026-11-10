"""Freeze deployed settings for the lifetime of a runtime container."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from sftpwarden.config import SFTPWardenConfig, load_config, runtime_config_fingerprints
from sftpwarden.utils.errors import RuntimeError, SFTPWardenError
from sftpwarden.utils.persistence import atomic_bytes, reject_symlink, sync_directory

APPLIED_CONFIG_PATH = Path("/run/sftpwarden/applied-config.json")
SSHD_CONFIG_PATH = Path("/etc/ssh/sshd_config")


def load_runtime_config(config_path: str | Path) -> SFTPWardenConfig:
    """Read the private applied snapshot, never the editable desired config."""
    try:
        reject_symlink(APPLIED_CONFIG_PATH)
        record = json.loads(APPLIED_CONFIG_PATH.read_text(encoding="utf-8"))
        if record["format"] != 1 or record["source"] != str(Path(config_path).resolve()):
            raise ValueError("Applied config source mismatch")
        return SFTPWardenConfig.model_validate(record["config"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(
            "Applied runtime configuration is missing or invalid.",
            suggestion="Recreate the runtime with `sftpwarden deploy`.",
        ) from exc


def validate_sshd_candidate(candidate: Path) -> None:
    """Validate a staged daemon config without changing the running daemon."""
    subprocess.run(
        ["/usr/sbin/sshd", "-t", "-f", str(candidate)],
        check=True,
        timeout=5,
        capture_output=True,
    )


def prepare_runtime_configuration(config_path: str | Path) -> SFTPWardenConfig:
    """Validate daemon settings before publishing a first-start applied snapshot.

    The snapshot lives in the container layer. A restart keeps it; explicit
    deployment recreates the container and captures new desired settings.
    """
    from sftpwarden.runtime.core import render_sshd_config_text

    existing = APPLIED_CONFIG_PATH.exists() or APPLIED_CONFIG_PATH.is_symlink()
    config = load_runtime_config(config_path) if existing else load_config(config_path)
    descriptor, name = tempfile.mkstemp(prefix=".sftpwarden-sshd-", dir=SSHD_CONFIG_PATH.parent)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(render_sshd_config_text(config))
            handle.flush()
            os.fsync(handle.fileno())
        validate_sshd_candidate(candidate)
        reject_symlink(SSHD_CONFIG_PATH)
        os.replace(candidate, SSHD_CONFIG_PATH)
        sync_directory(SSHD_CONFIG_PATH.parent)
        if not existing:
            atomic_bytes(
                APPLIED_CONFIG_PATH,
                json.dumps(
                    {
                        "format": 1,
                        "source": str(Path(config_path).resolve()),
                        "config": config.model_dump(mode="json"),
                    }
                ).encode("utf-8"),
            )
    finally:
        candidate.unlink(missing_ok=True)
    return config


def check_desired_provider(
    config_path: str | Path,
    *,
    desired_provider_fingerprint: str | None = None,
    desired_config_fingerprint: str | None = None,
) -> bool:
    """Reject refresh against an undeployed provider; report other pending config edits."""
    applied = load_runtime_config(config_path)
    if desired_provider_fingerprint is not None or desired_config_fingerprint is not None:
        if desired_provider_fingerprint is None or desired_config_fingerprint is None:
            raise RuntimeError("Both desired configuration fingerprints are required.")
        provider_digest, config_digest = runtime_config_fingerprints(applied)
        if desired_provider_fingerprint != provider_digest:
            raise RuntimeError(
                "Provider configuration changed and requires deploy before refresh.",
                suggestion="Run `sftpwarden deploy` to activate the desired provider settings.",
            )
        return desired_config_fingerprint != config_digest
    try:
        desired = load_config(config_path)
    except (OSError, ValueError, yaml.YAMLError, SFTPWardenError) as exc:
        raise RuntimeError(
            "Desired configuration is invalid; the deployed configuration remains active.",
            suggestion="Repair the desired config and run `sftpwarden deploy`.",
        ) from exc
    if desired.provider != applied.provider:
        raise RuntimeError(
            "Provider configuration changed and requires deploy before refresh.",
            suggestion="Run `sftpwarden deploy` to activate the desired provider settings.",
        )
    return desired != applied
