"""Short-lived discovery worker; never imports the CLI or contacts user environments."""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

from packaging.version import Version

from sftpwarden.services.releases import GITHUB_RELEASES, PYPI_PROJECT, select_release


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep public metadata requests on their exact approved endpoints."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> Any:
        """Refuse endpoint redirection instead of forwarding it."""
        raise ValueError("Release metadata redirects are not accepted")


def fetch(url: str) -> Any:
    """Read size-bounded JSON from the two public release metadata endpoints."""
    prefix = "https://raw.githubusercontent.com/kithuto/SFTPWarden/v"
    suffix = "/sftpwarden/release_compatibility.json"
    if url not in {PYPI_PROJECT, GITHUB_RELEASES} and not (
        url.startswith(prefix)
        and url.endswith(suffix)
        and str(Version(url[len(prefix) : -len(suffix)])) == url[len(prefix) : -len(suffix)]
    ):
        raise ValueError("Unapproved release endpoint")
    request = urllib.request.Request(  # noqa: S310 - exact HTTPS allowlist above
        url, headers={"User-Agent": "SFTPWarden-release-check"}
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=4) as response:
        content = response.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise ValueError("Oversized release metadata")
        return json.loads(content)


def main() -> int:
    """Emit only validated release metadata, never network error details."""
    try:
        target = sys.argv[1] if len(sys.argv) > 1 else None
        from sftpwarden.services.update_compatibility import validate_compatibility

        version = select_release(fetch(PYPI_PROJECT), fetch(GITHUB_RELEASES), target)
        result: dict[str, Any] = {"version": version}
        if Version(version) >= Version("1.4.0"):
            result["compatibility"] = validate_compatibility(
                fetch(
                    f"https://raw.githubusercontent.com/kithuto/SFTPWarden/v{version}/sftpwarden/release_compatibility.json"
                ),
                version,
            )
        print(json.dumps(result))
    except Exception:  # noqa: BLE001
        # Network responses and proxy credentials must never reach CLI diagnostics.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
