from __future__ import annotations

from rich.prompt import Confirm

from sftpwarden.config import (
    DeployTarget,
    KubernetesMode,
    ProviderType,
    SFTPWardenConfig,
    load_config,
)
from sftpwarden.contexts import ContextEntry
from sftpwarden.refresh import refresh_context
from sftpwarden.utils.console import console, print_info, terminal_status
from sftpwarden.watcher import (
    WatcherDockerFallbackRequired,
    ensure_watcher,
    installed_watcher_mode,
)


def print_refresh_after_user_change(entry: ContextEntry) -> None:
    """Apply or explain the next runtime step after a provider user change.

    Parameters
    ----------
    entry
        Context affected by a user mutation.
    """
    kubernetes_config = _kubernetes_config(entry)
    if kubernetes_config:
        if kubernetes_config.provider.type in {ProviderType.YAML, ProviderType.CSV}:
            action = "`sftpwarden deploy`"
            extra = "`sftpwarden kube apply`"
            if kubernetes_config.kubernetes.mode == KubernetesMode.HELM:
                extra = "`sftpwarden helm upgrade --install`"
            print_info(
                "Saved provider change locally. Kubernetes and Helm sync YAML/CSV "
                f"providers to the provider PVC during {action}; you can also run {extra}."
            )
            return
        if kubernetes_config.provider.type == ProviderType.SQLITE:
            print_info(
                "Saved SQLite provider change locally. SQLite provider files are not copied "
                "into Kubernetes PVCs automatically; use YAML/CSV deploy sync for declarative "
                "file providers or a database provider for production Kubernetes."
            )
            return
    with terminal_status(f"Refreshing context {entry.name}"):
        output = refresh_context(entry)
    console.print(output)


def _kubernetes_config(entry: ContextEntry) -> SFTPWardenConfig | None:
    """Return project config only for Kubernetes deployment contexts."""
    if not entry.config:
        return None
    config = load_config(entry.config)
    if config.deploy.target != DeployTarget.KUBERNETES:
        return None
    return config


def install_context_watcher(
    entry: ContextEntry,
    *,
    requested_mode: str | None,
    yes: bool,
) -> None:
    """Install or reuse the watcher required by a context.

    Parameters
    ----------
    entry
        Context that may require a watcher.
    requested_mode
        Optional watcher mode requested by the caller.
    yes
        Whether confirmation prompts should be skipped.
    """
    if not entry.watcher_required:
        return
    existing = installed_watcher_mode()
    replace = False
    if existing and requested_mode and existing.value != requested_mode:
        replace = yes or Confirm.ask(
            f"Replace existing {existing.value} watcher with {requested_mode}?", default=False
        )
        if not replace:
            print_info(f"Using existing [bold]{existing.value}[/bold] watcher.")
            return
    try:
        result = ensure_watcher(
            requested_mode=requested_mode,
            yes=yes or replace,
            allow_docker_fallback=yes or replace,
        )
    except WatcherDockerFallbackRequired:
        if not Confirm.ask(
            "No supported native watcher scheduler was detected. "
            "Install the Docker watcher instead?",
            default=False,
        ):
            raise
        result = ensure_watcher(
            requested_mode=requested_mode,
            yes=True,
            allow_docker_fallback=True,
        )
    console.print(result)
