from __future__ import annotations

import os
import re
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sftpwarden.config import ProviderType, RemoteStorage, load_config
from sftpwarden.utils.constants import CONFIG_FILENAME, DEFAULT_SSH_PORT, PRODUCTION_NAMES
from sftpwarden.utils.errors import ContextError
from sftpwarden.utils.paths import contexts_path, expand_path


class ContextType(StrEnum):
    """Type of registered SFTPWarden context."""

    LOCAL = "local"
    REMOTE = "remote"


class RemoteEndpoint(BaseModel):
    """SSH endpoint and remote SFTPWarden paths."""

    model_config = ConfigDict(extra="forbid")

    host: str
    user: str
    port: int = DEFAULT_SSH_PORT
    remote_root: str
    remote_config: str
    ssh_key: str | None = None
    compose_file: str = "docker-compose.yml"


class ContextEntry(BaseModel):
    """Registered local or remote SFTPWarden context."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: ContextType
    root: str = ""
    config: str = ""
    provider: ProviderType = ProviderType.YAML
    critical: bool = False
    storage: RemoteStorage | None = None
    watcher_required: bool = False
    remote: RemoteEndpoint | None = None


class ContextRegistry(BaseModel):
    """Persistent registry of known SFTPWarden contexts."""

    model_config = ConfigDict(extra="forbid")

    default: str | None = None
    contexts: dict[str, ContextEntry] = Field(default_factory=dict)


@dataclass(frozen=True)
class ParsedRemoteURL:
    """Parsed remote URL components."""

    user: str | None
    host: str
    path: str | None


REMOTE_RE = re.compile(r"^(?:(?P<user>[^@\s:]+)@)?(?P<host>[^:\s]+)(?::(?P<path>.+))?$")


def is_production_like(name: str) -> bool:
    """Return whether a context name looks production-like.

    Parameters
    ----------
    name
        Context name.

    Returns
    -------
    bool
        ``True`` when the name should receive production safeguards.
    """
    return name.strip().lower() in PRODUCTION_NAMES


def parse_remote_url(value: str) -> ParsedRemoteURL:
    """Parse a remote URL in ``[user@]host[:path]`` form.

    Parameters
    ----------
    value
        Remote URL string.

    Returns
    -------
    ParsedRemoteURL
        Parsed user, host, and optional path.

    Raises
    ------
    ContextError
        Raised when the URL is invalid.
    """
    match = REMOTE_RE.match(value)
    if not match:
        raise ContextError(
            f"Invalid remote URL: {value}",
            suggestion="Use the form [user@]host[:/absolute/path].",
        )
    return ParsedRemoteURL(
        user=match.group("user"),
        host=match.group("host"),
        path=match.group("path"),
    )


def load_registry(path: Path | None = None) -> ContextRegistry:
    """Load the context registry.

    Parameters
    ----------
    path
        Optional registry path.

    Returns
    -------
    ContextRegistry
        Loaded registry, or an empty registry when missing.
    """
    registry_path = path or contexts_path()
    if not registry_path.exists():
        return ContextRegistry()
    try:
        data = tomllib.loads(registry_path.read_text(encoding="utf-8"))
        return ContextRegistry.model_validate(data)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ContextError(
            f"Invalid context registry: {registry_path}",
            suggestion="Fix contexts.toml or remove it and register contexts again.",
        ) from exc


def save_registry(registry: ContextRegistry, path: Path | None = None) -> None:
    """Persist the context registry.

    Parameters
    ----------
    registry
        Registry to save.
    path
        Optional registry path.
    """
    registry_path = path or contexts_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        tomli_w.dumps(registry.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    with suppress(OSError):
        os.chmod(registry_path, 0o600)


def has_initialized_context(*, cwd: Path | None = None) -> bool:
    """Return whether at least one context or local project exists.

    Parameters
    ----------
    cwd
        Optional working directory used to detect an initialized local project.

    Returns
    -------
    bool
        ``True`` when the registry has contexts or the working directory contains
        ``sftpwarden.yaml``.
    """
    registry = load_registry()
    if registry.contexts:
        return True
    working_dir = cwd or Path.cwd()
    return (working_dir / CONFIG_FILENAME).exists()


def require_initialized_context(*, cwd: Path | None = None) -> None:
    """Require that SFTPWarden has been initialized at least once.

    Parameters
    ----------
    cwd
        Optional working directory used to detect an initialized local project.

    Raises
    ------
    ContextError
        Raised when no context registry entry or local project config exists.
    """
    if has_initialized_context(cwd=cwd):
        return
    raise ContextError(
        "No SFTPWarden context has been initialized.",
        suggestion="Run `sftpwarden init <name>` first.",
    )


def register_context(entry: ContextEntry) -> ContextRegistry:
    """Register or replace a context.

    Parameters
    ----------
    entry
        Context entry to save.

    Returns
    -------
    ContextRegistry
        Updated registry.
    """
    registry = load_registry()
    registry.contexts[entry.name] = entry
    if registry.default is None:
        registry.default = entry.name
    save_registry(registry)
    return registry


def remove_context(name: str) -> ContextRegistry:
    """Remove a registered context.

    Parameters
    ----------
    name
        Context name to remove.

    Returns
    -------
    ContextRegistry
        Updated registry.
    """
    registry = load_registry()
    if name not in registry.contexts:
        raise ContextError(f"Unknown context: {name}", suggestion="Run `sftpwarden context ls`.")
    del registry.contexts[name]
    if registry.default == name:
        registry.default = next(iter(registry.contexts), None)
    save_registry(registry)
    return registry


def set_default_context(name: str) -> ContextRegistry:
    """Set the default context.

    Parameters
    ----------
    name
        Context name.

    Returns
    -------
    ContextRegistry
        Updated registry.
    """
    registry = load_registry()
    if name not in registry.contexts:
        raise ContextError(f"Unknown context: {name}", suggestion="Run `sftpwarden context ls`.")
    registry.default = name
    save_registry(registry)
    return registry


def reconcile_registered_context(registry: ContextRegistry, name: str) -> ContextEntry:
    """Sync registry metadata from the local project config when available.

    Parameters
    ----------
    registry
        Loaded context registry.
    name
        Context key to reconcile.

    Returns
    -------
    ContextEntry
        Reconciled context entry.

    Raises
    ------
    ContextError
        Raised when a manually changed project name conflicts with another
        registered context.
    """
    entry = reconcile_registered_paths(registry, name)
    if not entry.config:
        return entry

    config_path = expand_path(entry.config)
    if not config_path.exists():
        return entry

    config = load_config(config_path)
    updates: dict[str, object] = {}
    if entry.provider != config.provider.type:
        updates["provider"] = config.provider.type
    if entry.name != config.project.name:
        if config.project.name in registry.contexts and config.project.name != name:
            raise ContextError(f"Context already exists: {config.project.name}")
        updates["name"] = config.project.name

    if not updates:
        return entry

    updated = entry.model_copy(update=updates)
    if updated.name != name:
        del registry.contexts[name]
        registry.contexts[updated.name] = updated
        if registry.default == name:
            registry.default = updated.name
    else:
        registry.contexts[name] = updated
    save_registry(registry)
    return updated


def reconcile_registered_paths(registry: ContextRegistry, name: str) -> ContextEntry:
    """Detect and reconcile safe root/config path changes.

    Parameters
    ----------
    registry
        Loaded context registry.
    name
        Context key to reconcile.

    Returns
    -------
    ContextEntry
        Context with consistent root/config paths.

    Raises
    ------
    ContextError
        Raised when a manual root edit needs an explicit migration command.
    """
    entry = registry.contexts[name]
    if not entry.root or not entry.config:
        return entry

    root = expand_path(entry.root)
    config_path = expand_path(entry.config)
    expected_config = root / CONFIG_FILENAME
    if config_path.parent == root:
        return entry

    if expected_config.exists():
        updated = entry.model_copy(update={"config": str(expected_config)})
        registry.contexts[name] = updated
        save_registry(registry)
        return updated

    if config_path.exists():
        raise ContextError(
            f"Context {entry.name} has inconsistent root/config paths.",
            suggestion=(
                f"Run `sftpwarden context root {root} --yes` so SFTPWarden can copy "
                "project files safely, or update both root and config to an existing project."
            ),
        )
    return entry


def resolve_context(
    *,
    config_path: str | None = None,
    context_name: str | None = None,
    cwd: Path | None = None,
    reconcile_config: bool = False,
) -> ContextEntry:
    """Resolve the active context.

    Parameters
    ----------
    config_path
        Optional explicit project config path.
    context_name
        Optional registered context name.
    cwd
        Optional working directory used for local discovery.
    reconcile_config
        Whether to reconcile registry metadata from the project config.

    Returns
    -------
    ContextEntry
        Resolved context.

    Raises
    ------
    ContextError
        Raised when no context can be resolved.
    """
    working_dir = cwd or Path.cwd()
    if config_path:
        path = expand_path(config_path)
        config = load_config(path)
        return ContextEntry(
            name=config.project.name,
            type=ContextType.LOCAL,
            root=str(path.parent),
            config=str(path),
            provider=config.provider.type,
        )
    requested = context_name or os.environ.get("SFTPWARDEN_CONTEXT")
    registry = load_registry()
    if requested:
        try:
            if reconcile_config:
                return reconcile_registered_context(registry, requested)
            return registry.contexts[requested]
        except KeyError as exc:
            raise ContextError(
                f"Unknown context: {requested}", suggestion="Run `sftpwarden context ls`."
            ) from exc
    if registry.default and registry.default in registry.contexts:
        if reconcile_config:
            return reconcile_registered_context(registry, registry.default)
        return registry.contexts[registry.default]
    local_config = working_dir / CONFIG_FILENAME
    if local_config.exists():
        config = load_config(local_config)
        return ContextEntry(
            name=config.project.name,
            type=ContextType.LOCAL,
            root=str(working_dir),
            config=str(local_config),
            provider=config.provider.type,
        )
    require_initialized_context(cwd=working_dir)
    raise ContextError(
        "No active SFTPWarden context could be resolved.",
        suggestion="Run `sftpwarden context use <name>` or pass --context.",
    )


def local_context(
    name: str, root: str | Path, provider: ProviderType, critical: bool = False
) -> ContextEntry:
    """Create a local context entry.

    Parameters
    ----------
    name
        Context name.
    root
        Local project root.
    provider
        Provider type.
    critical
        Whether the context should require critical-operation confirmation.

    Returns
    -------
    ContextEntry
        Local context entry.
    """
    root_path = expand_path(root)
    return ContextEntry(
        name=name,
        type=ContextType.LOCAL,
        root=str(root_path),
        config=str(root_path / CONFIG_FILENAME),
        provider=provider,
        critical=critical,
    )


def remote_context(
    *,
    name: str,
    provider: ProviderType,
    remote_url: str,
    local_root: str | Path | None,
    remote_root: str,
    remote_only: bool,
    ssh_key: str | None,
    critical: bool,
    remote_user: str | None = None,
    explicit_remote_root: str | None = None,
    port: int = DEFAULT_SSH_PORT,
) -> ContextEntry:
    """Create a remote context entry.

    Parameters
    ----------
    name
        Context name.
    provider
        Provider type.
    remote_url
        Remote URL in ``[user@]host[:path]`` form.
    local_root
        Optional local project root for local-sync contexts.
    remote_root
        Default remote root.
    remote_only
        Whether the context stores files only on the remote host.
    ssh_key
        Optional SSH key path or ``default``.
    critical
        Whether the context should require critical-operation confirmation.
    remote_user
        Optional explicit remote user.
    explicit_remote_root
        Optional explicit remote root.
    port
        SSH port.

    Returns
    -------
    ContextEntry
        Remote context entry.
    """
    parsed = parse_remote_url(remote_url)
    if parsed.user and remote_user and parsed.user != remote_user:
        raise ContextError(
            "Remote URL user and --user do not match.",
            suggestion="Use either the URL user or --user, or make both values equal.",
        )
    final_user = parsed.user or remote_user
    if not final_user:
        raise ContextError(
            "Remote user is required.",
            suggestion="Use user@host:/path or pass --user.",
        )
    if parsed.path and explicit_remote_root and parsed.path != explicit_remote_root:
        raise ContextError(
            "Remote URL path and --remote-root do not match.",
            suggestion="Use either the URL path or --remote-root, or make both values equal.",
        )
    final_remote_root = parsed.path or explicit_remote_root or remote_root
    storage = RemoteStorage.REMOTE_ONLY if remote_only else RemoteStorage.LOCAL_SYNC
    root = "" if remote_only else str(expand_path(local_root or f"~/sftpwarden-{name}"))
    config = "" if remote_only else str(Path(root) / CONFIG_FILENAME)
    return ContextEntry(
        name=name,
        type=ContextType.REMOTE,
        storage=storage,
        root=root,
        config=config,
        provider=provider,
        critical=critical,
        watcher_required=storage == RemoteStorage.LOCAL_SYNC,
        remote=RemoteEndpoint(
            host=parsed.host,
            user=final_user,
            port=port,
            remote_root=final_remote_root,
            remote_config=f"{final_remote_root.rstrip('/')}/{CONFIG_FILENAME}",
            ssh_key=ssh_key,
        ),
    )
