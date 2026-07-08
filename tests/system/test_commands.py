from __future__ import annotations

import sys

import pytest

from sftpwarden.system.commands import command_text, run, run_checked
from sftpwarden.utils.errors import RuntimeError


def test_run_checked_returns_stdout() -> None:
    result = run_checked(
        ["python", "-c", "print('ok')"],
        error_type=RuntimeError,
        message="failed",
        fallback_suggestion="fallback",
    )

    assert result.stdout.strip() == "ok"


def test_run_checked_raises_with_stderr() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        run_checked(
            ["python", "-c", "import sys; sys.stderr.write('bad'); raise SystemExit(2)"],
            error_type=RuntimeError,
            message="failed",
            fallback_suggestion="fallback",
        )

    assert exc_info.value.message == "failed"
    assert exc_info.value.suggestion == "bad"


def test_run_decodes_external_output_with_replacement() -> None:
    result = run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\x81ok')",
        ]
    )

    assert result.returncode == 0
    assert result.stdout.endswith("ok")


def test_command_text_quotes_arguments() -> None:
    assert command_text(["ssh", "host", "cd /tmp/path with space"]) == (
        "ssh host 'cd /tmp/path with space'"
    )
