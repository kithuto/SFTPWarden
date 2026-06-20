from __future__ import annotations

import shutil
import subprocess


def test_installed_console_script_reports_version() -> None:
    executable = shutil.which("sftpwarden")

    assert executable is not None
    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "SFTPWarden" in result.stdout
