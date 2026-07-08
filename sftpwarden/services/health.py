from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, RemoteStorage, load_config, provider_local_path
from sftpwarden.contexts import ContextEntry, ContextType, resolve_context
from sftpwarden.refresh import docker_compose_command
from sftpwarden.remote.ssh import ssh_base_command
from sftpwarden.render.compose import compose_text
from sftpwarden.services.context_cleanup import ensure_remote_only_root_available
from sftpwarden.services.provider_schema import plan_provider_schema_reconciliation
from sftpwarden.system.commands import command_text, run
from sftpwarden.utils.constants import CONTAINER_CONFIG_PATH
from sftpwarden.utils.errors import ContextError

RUNTIME_AUTHORIZED_KEYS_DIR = Path("/etc/sftpwarden/authorized_keys")
RUNTIME_SSHD_CONFIG = Path("/etc/ssh/sshd_config")


@dataclass(frozen=True)
class HealthCheck:
    """One health check result."""

    name: str
    status: str
    message: str
    suggestion: str | None = None


@dataclass(frozen=True)
class HealthReport:
    """Structured health report."""

    context: str
    checks: list[HealthCheck]

    @property
    def healthy(self) -> bool:
        """Return whether every health check passed.

        Returns
        -------
        bool
            ``True`` when no check failed.
        """
        return all(check.status != "fail" for check in self.checks)

    def as_dict(self) -> dict:
        """Return a JSON-compatible health report.

        Returns
        -------
        dict
            JSON-compatible health report.
        """
        return {
            "healthy": self.healthy,
            "context": self.context,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "message": check.message,
                    "suggestion": check.suggestion,
                }
                for check in self.checks
            ],
        }


def project_health(context_name: str | None = None) -> HealthReport:
    """Build a project health report.

    Parameters
    ----------
    context_name
        Optional context name.

    Returns
    -------
    HealthReport
        Project health report.
    """
    checks: list[HealthCheck] = []
    entry = resolve_context(context_name=context_name)
    if entry.type == ContextType.REMOTE and entry.storage == RemoteStorage.REMOTE_ONLY:
        try:
            ensure_remote_only_root_available(entry)
        except ContextError as exc:
            return HealthReport(
                context=entry.name,
                checks=[HealthCheck("remote", "fail", exc.message, exc.suggestion)],
            )
        return HealthReport(context=entry.name, checks=runtime_health_from_context(entry))
    if not entry.root or not entry.config:
        return HealthReport(
            context=entry.name,
            checks=[
                HealthCheck(
                    name="context",
                    status="fail",
                    message="Context has no local config.",
                    suggestion="Use a local or remote local-sync context.",
                )
            ],
        )

    config_path = Path(entry.config)
    root = Path(entry.root)
    try:
        config = load_config(config_path)
        checks.append(HealthCheck("config", "pass", f"Loaded {config_path}."))
    except Exception as exc:  # noqa: BLE001
        return HealthReport(
            context=entry.name,
            checks=[HealthCheck("config", "fail", str(exc), "Fix sftpwarden.yaml.")],
        )

    try:
        schema_result = plan_provider_schema_reconciliation(entry, config)
        if schema_result.changed:
            checks.append(
                HealthCheck(
                    "provider",
                    "warn",
                    (
                        f"Provider data schema v{schema_result.from_schema} will be migrated "
                        f"to configured schema v{schema_result.to_schema}."
                    ),
                    "Run `sftpwarden deploy` to apply the provider schema migration.",
                )
            )
        else:
            checks.append(HealthCheck("provider", "pass", f"Read {config.provider.type.value}."))
    except Exception as exc:  # noqa: BLE001
        checks.append(HealthCheck("provider", "fail", str(exc), "Verify provider configuration."))

    if config.provider.type in FILE_PROVIDER_TYPES:
        provider_path = provider_local_path(root, config)
        status = "pass" if provider_path.exists() else "fail"
        checks.append(
            HealthCheck(
                "provider-file",
                status,
                f"Provider file: {provider_path}",
                None if status == "pass" else "Run `sftpwarden init` or restore the file.",
            )
        )

    compose_path = root / config.docker.compose_file
    expected = compose_text(config, root)
    if not compose_path.exists():
        checks.append(
            HealthCheck(
                "compose",
                "warn",
                f"{config.docker.compose_file} is missing.",
                "Run `sftpwarden compose --write` or `sftpwarden deploy`.",
            )
        )
    elif compose_path.read_text(encoding="utf-8") != expected:
        checks.append(
            HealthCheck(
                "compose",
                "warn",
                f"{config.docker.compose_file} differs from config.",
                "Run `sftpwarden deploy`.",
            )
        )
    else:
        checks.append(HealthCheck("compose", "pass", f"{config.docker.compose_file} is current."))

    checks.extend(runtime_health_from_context(entry))
    return HealthReport(context=entry.name, checks=checks)


def runtime_health_from_context(entry: ContextEntry) -> list[HealthCheck]:
    """Run runtime health through Docker Compose when possible.

    Parameters
    ----------
    entry
        Context entry.

    Returns
    -------
    list[HealthCheck]
        Runtime checks.
    """
    command = [
        *docker_compose_command(entry)[:-1],
        "health",
        "--config",
        CONTAINER_CONFIG_PATH,
        "--json",
    ]
    if entry.type == ContextType.LOCAL:
        result = run(command, cwd=entry.root or ".")
    elif entry.remote:
        remote_root = shlex.quote(entry.remote.remote_root)
        remote_command = f"cd {remote_root} && {command_text(command)}"
        result = run([*ssh_base_command(entry.remote), remote_command])
    else:
        return [HealthCheck("runtime", "fail", "Remote context is missing settings.")]

    if result.returncode == 0:
        return [HealthCheck("runtime", "pass", "Runtime health command passed.")]
    return [
        HealthCheck(
            "runtime",
            "warn",
            result.output or "Runtime health command did not pass.",
            "Start the runtime with `sftpwarden deploy` if it is not running.",
        )
    ]


def runtime_health(config_path: str | Path = CONTAINER_CONFIG_PATH) -> HealthReport:
    """Build a runtime-internal health report.

    Parameters
    ----------
    config_path
        Runtime config path.

    Returns
    -------
    HealthReport
        Runtime health report.
    """
    checks: list[HealthCheck] = []
    try:
        config = load_config(config_path)
        checks.append(HealthCheck("config", "pass", f"Loaded {config_path}."))
    except Exception as exc:  # noqa: BLE001
        return HealthReport(
            context="runtime",
            checks=[HealthCheck("config", "fail", str(exc), "Fix runtime config.")],
        )

    for name, path in {
        "state-dir": Path(config.server.state_dir),
        "data-dir": Path(config.server.data_dir),
        "host-keys-dir": Path(config.server.host_keys_dir),
        "authorized-keys-dir": RUNTIME_AUTHORIZED_KEYS_DIR,
    }.items():
        if path.exists():
            checks.append(HealthCheck(name, "pass", f"{path} exists."))
        else:
            checks.append(HealthCheck(name, "fail", f"{path} does not exist."))

    try:
        from sftpwarden.runtime import load_runtime_inputs

        load_runtime_inputs(config_path)
        checks.append(HealthCheck("provider", "pass", "Provider users are readable."))
    except Exception as exc:  # noqa: BLE001
        checks.append(
            HealthCheck("provider", "fail", str(exc), "Verify provider config and dependencies.")
        )

    sshd_config = RUNTIME_SSHD_CONFIG
    checks.append(
        HealthCheck(
            "sshd-config",
            "pass" if sshd_config.exists() else "fail",
            f"{sshd_config} {'exists' if sshd_config.exists() else 'does not exist'}.",
        )
    )
    return HealthReport(context="runtime", checks=checks)
