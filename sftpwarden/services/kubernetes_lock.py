"""Explicit cluster operation ownership shared by deployment and recovery."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from sftpwarden.config import SFTPWardenConfig
from sftpwarden.render.kubernetes import kubernetes_resource_name
from sftpwarden.services.kubernetes_identity import get_resource, request, write_resource
from sftpwarden.utils.errors import RuntimeError


@contextmanager
def operation_lock(config: SFTPWardenConfig, purpose: str) -> Iterator[None]:
    """Never steal a stale lock; require inspection of an interrupted operation."""
    name = kubernetes_resource_name(config.kubernetes.release) + "-operation"
    nonce = uuid4().hex
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": config.kubernetes.namespace,
            "labels": {"sftpwarden.io/operation-owner": nonce},
        },
        "data": {"purpose": purpose},
    }
    try:
        write_resource(config, document, action="create")
        yield
    finally:
        # An uncertain create can have succeeded; only our exact receipt is removable.
        current = get_resource(config, "configmap", name)
        if (
            current
            and current["metadata"].get("labels", {}).get("sftpwarden.io/operation-owner") == nonce
        ):
            try:
                request(config, ["delete", "configmap", name, "--wait=true", "--timeout=30s"])
            except RuntimeError:
                raise RuntimeError(
                    f"Operation lock cleanup could not be confirmed: {name}. "
                    "Inspect the operation before removing this lock."
                ) from None
