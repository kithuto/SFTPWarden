from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import sftpwarden


def test_package_version_matches_module_version() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == sftpwarden.__version__


def test_package_metadata_is_public_release_ready() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["version"] == "1.0.0"
    assert "Development Status :: 5 - Production/Stable" in project["classifiers"]
    assert project["license"] == "Apache-2.0"
    assert project["readme"] == "README.md"
    assert project["urls"]["Documentation"] == "https://kithuto.github.io/sftpwarden/"


def test_installed_console_script_reports_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sftpwarden", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "SFTPWarden" in result.stdout
    assert sftpwarden.__version__ in result.stdout
