"""Packaging, metadata, and public release readiness tests."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import yaml

import sftpwarden
import sftpwarden.utils._version as version_module
from sftpwarden.utils._version import (
    _is_distribution_pyproject,
    _pyproject_path,
    _read_pyproject_version,
    get_version,
)


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


def test_package_version_is_read_from_pyproject() -> None:
    """Keep the runtime package version aligned with project metadata."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == sftpwarden.__version__


def test_version_pyproject_must_match_distribution_name(tmp_path: Path) -> None:
    """Read versions only from the SFTPWarden distribution metadata."""
    other_project = tmp_path / "pyproject.toml"
    other_project.write_text(
        '[project]\nname = "other-project"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )

    sftpwarden_project = tmp_path / "sftpwarden.toml"
    sftpwarden_project.write_text(
        '[project]\nname = "sftpwarden"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    assert not _is_distribution_pyproject(other_project)
    assert _is_distribution_pyproject(sftpwarden_project)
    assert _read_pyproject_version(sftpwarden_project) == "1.2.3"


def test_version_helpers_ignore_invalid_pyprojects_and_fall_back_to_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    """Fall back to installed metadata when local pyproject data is invalid."""
    missing_project = tmp_path / "missing-project.toml"
    missing_project.write_text('name = "sftpwarden"\n', encoding="utf-8")
    invalid_toml = tmp_path / "invalid.toml"
    invalid_toml.write_text("project = [\n", encoding="utf-8")

    assert not _is_distribution_pyproject(missing_project)
    assert not _is_distribution_pyproject(invalid_toml)

    monkeypatch.setattr(version_module, "_is_distribution_pyproject", lambda _path: False)
    assert _pyproject_path() is None

    monkeypatch.setattr(version_module, "_pyproject_path", lambda: None)
    monkeypatch.setattr(version_module.metadata, "version", lambda _name: "9.9.9")
    assert get_version() == "9.9.9"


def test_package_metadata_is_public_release_ready() -> None:
    """Validate public package metadata required for a stable release."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["version"] == get_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", project["version"])
    assert "Development Status :: 5 - Production/Stable" in project["classifiers"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["readme"] == "README.md"
    assert project["urls"]["Documentation"] == "https://kithuto.github.io/sftpwarden/"


def test_release_versions_are_consistent() -> None:
    """Keep package, chart, generated image tags, examples, and changelog aligned."""
    version = get_version()
    chart = yaml.safe_load(Path("charts/sftpwarden/Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load(Path("charts/sftpwarden/values.yaml").read_text(encoding="utf-8"))
    example_values = yaml.safe_load(
        Path("examples/kubernetes/values-postgresql.yaml").read_text(encoding="utf-8")
    )
    changelog_headings = [
        line
        for line in Path("CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## [") and line != "## [Unreleased]"
    ]

    assert sftpwarden.__version__ == version
    assert chart["version"] == version
    assert chart["appVersion"] == version
    assert values["image"]["tag"] == ""
    assert example_values["image"]["tag"] == ""
    assert changelog_headings[0].startswith(f"## [{version}] - ")


def test_examples_are_cli_first_guides() -> None:
    """Keep public examples guided by SFTPWarden commands instead of static Compose files."""
    example_dirs = sorted(path for path in Path("examples").iterdir() if path.is_dir())
    static_compose_files = sorted(Path("examples").glob("*/docker-compose.yml"))
    missing_readmes = [path for path in example_dirs if not (path / "README.md").is_file()]

    assert example_dirs
    assert static_compose_files == []
    assert missing_readmes == []
    for readme in [Path("examples/README.md"), *(path / "README.md" for path in example_dirs)]:
        text = readme.read_text(encoding="utf-8")
        assert "sftpwarden " in text


def test_helm_release_metadata_script_enforces_empty_values_tag(tmp_path: Path) -> None:
    """Use the same Helm release metadata check in tests and publish workflows."""
    script = Path("tools/verify_helm_release_metadata.py")
    version = get_version()
    success = subprocess.run(
        [sys.executable, str(script), "--version", version],
        check=False,
        capture_output=True,
        text=True,
    )

    chart = tmp_path / "Chart.yaml"
    values = tmp_path / "values.yaml"
    chart.write_text(
        f'apiVersion: v2\nname: sftpwarden\nversion: {version}\nappVersion: "{version}"\n',
        encoding="utf-8",
    )
    values.write_text(
        f'image:\n  repository: ghcr.io/kithuto/sftpwarden\n  tag: "{version}"\n',
        encoding="utf-8",
    )
    failure = subprocess.run(
        [
            sys.executable,
            str(script),
            "--version",
            version,
            "--chart",
            str(chart),
            "--values",
            str(values),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert success.returncode == 0, success.stderr
    assert failure.returncode == 1
    assert "values.yaml image.tag must stay empty" in failure.stderr


def test_database_extras_cover_public_provider_aliases() -> None:
    """Expose database provider extras through the documented aliases."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]

    assert extras["mysql"] == extras["mariadb"] == ["pymysql"]
    assert extras["mongodb"] == ["pymongo"]


def test_readme_uses_pypi_safe_logo_url() -> None:
    """Use an absolute README logo URL that renders correctly on PyPI."""
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
    """Verify the packaged console entrypoint can report the version."""
    flag_result = subprocess.run(
        [sys.executable, "-m", "sftpwarden", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    command_result = subprocess.run(
        [sys.executable, "-m", "sftpwarden", "version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert flag_result.returncode == 0
    assert command_result.returncode == 0
    assert command_result.stdout == flag_result.stdout
    assert "SFTPWarden" in command_result.stdout
    assert sftpwarden.__version__ in command_result.stdout
