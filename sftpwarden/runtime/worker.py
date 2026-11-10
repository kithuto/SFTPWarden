"""Internal subprocess entry point; never expose provider exception payloads."""

from __future__ import annotations

import signal
import sys

from sftpwarden.runtime.core import apply_once, run_sync_loop


def _terminate(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    """Run one isolated pass or the long-lived synchronization worker."""
    signal.signal(signal.SIGTERM, _terminate)
    try:
        if sys.argv[1] == "--once":
            print(apply_once(sys.argv[2], force="--force" in sys.argv[3:]))
        else:
            run_sync_loop(sys.argv[1])
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001
        print("Runtime reconciliation failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
