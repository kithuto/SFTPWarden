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
from sftpwarden.constants import CONFIG_FILENAME, DEFAULT_SSH_PORT, PRODUCTION_NAMES
from sftpwarden.errors import ContextError
from sftpwarden.paths import contexts_path, expand_path


class ContextType(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class RemoteEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    user: str
    port: int = DEFAULT_SSH_PORT
    remote_root: str
    remote_config: str
    ssh_key: str | None = None
    compose_file: str = "docker-compose.yml"


class ContextEntry(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    default: str | None = None
    contexts: dict[str, ContextEntry] = Field(default_factory=dict)


@dataclass(frozen=True)
class ParsedRemoteURL:
    user: str
    host: str
    path: str | None


REMOTE_RE = re.compile(r"^(?P<user>[^@\s:]+)@(?P<host>[^:\s]+)(?::(?P<path>.+))?$")


def is_production_like(name: str) -> bool:
    return name.strip().lower() in PRODUCTION_NAMES


def parse_remote_url(value: str) -> ParsedRemoteURL:
    match = REMOTE_RE.match(value)
    if not match:
        raise ContextError(
            f"Invalid remote URL: {value}",
            suggestion="Use the form user@host:/absolute/path.",
        )
    return ParsedRemoteURL(
        user=match.group("user"),
        host=match.group("host"),
        path=match.group("path"),
    )


def load_registry(path: Path | None = None) -> ContextRegistry:
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
    registry_path = path or contexts_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        tomli_w.dumps(registry.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    with suppress(OSError):
        os.chmod(registry_path, 0o600)


def register_context(entry: ContextEntry) -> ContextRegistry:
    registry = load_registry()
    registry.contexts[entry.name] = entry
    if registry.default is None:
        registry.default = entry.name
    save_registry(registry)
    return registry


def remove_context(name: str) -> ContextRegistry:
    registry = load_registry()
    if name not in registry.contexts:
        raise ContextError(f"Unknown context: {name}", suggestion="Run `sftpwarden context ls`.")
    del registry.contexts[name]
    if registry.default == name:
        registry.default = next(iter(registry.contexts), None)
    save_registry(registry)
    return registry


def set_default_context(name: str) -> ContextRegistry:
    registry = load_registry()
    if name not in registry.contexts:
        raise ContextError(f"Unknown context: {name}", suggestion="Run `sftpwarden context ls`.")
    registry.default = name
    save_registry(registry)
    return registry


def resolve_context(
    *,
    config_path: str | None = None,
    context_name: str | None = None,
    cwd: Path | None = None,
) -> ContextEntry:
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
            return registry.contexts[requested]
        except KeyError as exc:
            raise ContextError(
                f"Unknown context: {requested}", suggestion="Run `sftpwarden context ls`."
            ) from exc
    if registry.default and registry.default in registry.contexts:
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
    raise ContextError(
        "No SFTPWarden context could be resolved.",
        suggestion=(
            "Run `sftpwarden init <name>`, `sftpwarden context use <name>`, or pass --config."
        ),
    )


def local_context(
    name: str, root: str | Path, provider: ProviderType, critical: bool = False
) -> ContextEntry:
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
    port: int = DEFAULT_SSH_PORT,
) -> ContextEntry:
    parsed = parse_remote_url(remote_url)
    final_remote_root = parsed.path or remote_root
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
            user=parsed.user,
            port=port,
            remote_root=final_remote_root,
            remote_config=f"{final_remote_root.rstrip('/')}/{CONFIG_FILENAME}",
            ssh_key=ssh_key,
        ),
    )
