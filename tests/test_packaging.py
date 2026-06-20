from __future__ import annotations

import subprocess
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import sftpwarden


class _ImageSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        for name, value in attrs:
            if name == "src" and value:
                self.sources.append(value)


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


def test_readme_uses_pypi_safe_logo_url() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    parser = _ImageSourceParser()
    parser.feed(readme)

    logo_sources = [source for source in parser.sources if "logo-sftpwarden.png" in source]

    assert logo_sources == [
        "https://raw.githubusercontent.com/kithuto/SFTPWarden/main/docs/_static/logo-sftpwarden.png"
    ]
    parsed = urlparse(logo_sources[0])
    assert parsed.scheme == "https"
    assert parsed.netloc == "raw.githubusercontent.com"
    assert "logo%20sftpwarden" not in readme
    assert 'src="docs/_static/' not in readme


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
