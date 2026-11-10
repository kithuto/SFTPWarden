"""Recognize explicit missing manifests without masking registry access failures."""

from __future__ import annotations


def manifest_missing(diagnostic: str, reference: str) -> bool:
    """Accept known registry absence responses tied to the requested artifact."""
    diagnostic = diagnostic.lower().strip()
    if any(word in diagnostic for word in ("denied", "unauthorized", "forbidden")):
        return False
    if "manifest unknown" in diagnostic or "manifest_unknown" in diagnostic:
        return True
    reference = reference.removeprefix("oci://").lower()
    return diagnostic in {
        f"error: {reference}: not found",
        f'error: failed to perform "fetchreference" on source: {reference}: not found',
    }
