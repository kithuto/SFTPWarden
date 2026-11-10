"""Discover the owning package manager without mutating operator installations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

import tomli_w
from packaging.requirements import Requirement

from sftpwarden.services.releases import stable_version
from sftpwarden.utils._version import _pyproject_path
from sftpwarden.utils.errors import SFTPWardenError


@dataclass(frozen=True)
class Installation:
    """Pinned manager/interpreter identity for a single management update."""

    manager: str
    python: str
    prefix: str
    command: list[str]
    drivers: list[str]

    def as_dict(self) -> dict[str, Any]:
        """Return the private installer plan."""
        return asdict(self)


def installed_drivers() -> list[str]:
    """Record installed provider capabilities without inspecting credentials."""
    drivers = []
    for name in ("PyMySQL", "psycopg", "psycopg-binary", "pymongo"):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        drivers.append(name)
    return drivers


def manager_directory(command: list[str], expected: Path) -> None:
    """Ensure the manager on PATH actually owns this environment."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode or Path(result.stdout.strip()).resolve() != expected.resolve():
        raise SFTPWardenError("The package manager on PATH does not own this installation.")


def release_requirement(target: str, drivers: list[str]) -> str:
    """Retain installed provider capabilities when resolving the candidate."""
    extras = []
    if "PyMySQL" in drivers:
        extras.append("mysql")
    if "psycopg" in drivers or "psycopg-binary" in drivers:
        extras.append("postgres")
    if "pymongo" in drivers:
        extras.append("mongodb")
    return "sftpwarden" + (f"[{','.join(extras)}]" if extras else "") + f"=={target}"


def installation_plan(target: str) -> Installation:
    """Respect pipx/uv ownership; refuse source/system/container self-replacement."""
    target = str(stable_version(target))
    distribution = metadata.distribution("sftpwarden")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if _pyproject_path() or direct:
        raise SFTPWardenError(
            "This source or direct-URL installation cannot update itself from public releases.",
            suggestion="Update with the original package manager, then run deploy explicitly.",
        )
    prefix = Path(sys.prefix)
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        raise SFTPWardenError("Update this CLI container image instead of installing inside it.")
    if (
        sys.prefix == sys.base_prefix
        or (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists()
        and not (prefix / "pyvenv.cfg").exists()
    ):
        raise SFTPWardenError(
            "Self-update requires a writable virtual environment, pipx or uv tool installation.",
            suggestion="Use the owning system package manager; do not bypass its protections.",
        )
    if not os.access(str(distribution.locate_file("")), os.W_OK):
        raise SFTPWardenError("The current package installation is not writable.")
    pipx = prefix / "pipx_metadata.json"
    uv = prefix / "uv-receipt.toml"
    drivers = installed_drivers()
    if pipx.exists():
        manager = shutil.which("pipx")
        if not manager:
            raise SFTPWardenError("The owning pipx executable is unavailable.")
        data = json.loads(pipx.read_text(encoding="utf-8"))["main_package"]
        if data.get("package") != "sftpwarden" or data.get("suffix"):
            raise SFTPWardenError(
                "A custom pipx installation requires its original update command."
            )
        manager_directory([manager, "environment", "--value", "PIPX_LOCAL_VENVS"], prefix.parent)
        if data.get("pinned") or data.get("lock_file"):
            raise SFTPWardenError(
                "The pipx installation is pinned or locked; update its owner policy first."
            )
        # A constraint pins this invocation without discarding recorded pipx
        # sources/options. The worker adds PIP_CONSTRAINT/UV_CONSTRAINT privately.
        command = [manager, "upgrade", "sftpwarden"]
        owner = "pipx"
    elif uv.exists():
        manager = shutil.which("uv")
        if not manager:
            raise SFTPWardenError("The owning uv executable is unavailable.")
        manager_directory([manager, "tool", "dir"], prefix.parent)
        command = [manager, "tool", "install", "--force", release_requirement(target, drivers)]
        owner = "uv"
    else:
        if (distribution.read_text("INSTALLER") or "").strip() != "pip":
            raise SFTPWardenError("Unknown installation owner; use its original update command.")
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            release_requirement(target, drivers),
        ]
        owner = "pip"
    return Installation(owner, sys.executable, str(prefix), command, drivers)


def prepare_installation(
    installation: dict[str, Any], target: str, temporary: Path, environment: dict[str, str]
) -> None:
    """Resolve/install the candidate privately before stopping the real watcher.

    pip and pipx use an isolated target directory; uv installs into a private
    tools directory with the original indexes, options and declared extras.
    """
    owner = installation["manager"]
    prefix = Path(installation["prefix"])
    requirement = release_requirement(target, installation["drivers"])
    staged = temporary / "candidate"
    stage_environment = environment.copy()
    if owner == "uv":
        tools_root = temporary / "tools"
        staged = tools_root / prefix.name
        stage_environment.update(
            UV_TOOL_DIR=str(tools_root), UV_TOOL_BIN_DIR=str(temporary / "bin")
        )
        command = installer_command(installation, target, temporary, environment)
    elif owner == "pipx":
        data = json.loads((prefix / "pipx_metadata.json").read_text(encoding="utf-8"))[
            "main_package"
        ]
        original = Requirement(data["package_or_url"])
        if original.url:
            raise SFTPWardenError(
                "A direct-URL pipx installation requires its owning update procedure."
            )
        extras = sorted(original.extras | Requirement(requirement).extras)
        requirement = "sftpwarden" + (f"[{','.join(extras)}]" if extras else "") + f"=={target}"
        command = [
            installation["command"][0],
            "runpip",
            "sftpwarden",
            "install",
            "--target",
            str(staged),
            *data.get("pip_args", []),
            requirement,
        ]
    else:
        command = [
            installation["python"],
            "-m",
            "pip",
            "install",
            "--target",
            str(staged),
            "--ignore-installed",
            requirement,
        ]
    completed = subprocess.run(
        command, env=stage_environment, capture_output=True, timeout=600, check=False
    )
    if completed.returncode:
        raise SFTPWardenError(
            "Candidate preparation failed; the current CLI and watcher were not changed."
        )
    python = (
        str(staged / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        if owner == "uv"
        else installation["python"]
    )
    probe = (
        "import sys,importlib.metadata as m; from pathlib import Path; "
        + ("" if owner == "uv" else f"sys.path.insert(0, {str(staged)!r}); ")
        + "d=m.distribution('sftpwarden'); "
        + f"assert d.version=={target!r}; "
        + "assert Path(d.locate_file('')).resolve().is_relative_to("
        + f"Path({str(staged)!r}).resolve()); "
        + f"[m.version(n) for n in {installation['drivers']!r}]"
    )
    verified = subprocess.run(
        [python, "-I", "-c", probe], capture_output=True, timeout=30, check=False
    )
    if verified.returncode:
        raise SFTPWardenError(
            "The prepared candidate does not satisfy the version/provider requirements."
        )


def installer_command(
    installation: dict[str, Any], target: str, temporary: Path, environment: dict[str, str]
) -> list[str]:
    """Use uv's install operation to advance a version pin while retaining tool settings."""
    if installation["manager"] != "uv":
        return list(installation["command"])
    receipt = tomllib.loads((Path(installation["prefix"]) / "uv-receipt.toml").read_text())["tool"]
    requirements = []
    main = None
    for item in receipt["requirements"]:
        if set(item) - {"name", "specifier", "extras"}:
            raise SFTPWardenError(
                "This uv tool uses a custom source; update it with its original command."
            )
        extras = set(item.get("extras", []))
        specifier = item.get("specifier", "")
        if item["name"] == "sftpwarden":
            extras |= Requirement(release_requirement(target, installation["drivers"])).extras
            specifier = f"=={target}"
        requirement = item["name"] + (f"[{','.join(sorted(extras))}]" if extras else "") + specifier
        if item["name"] == "sftpwarden":
            main = requirement
        else:
            requirements.extend(["--with", requirement])
    if main is None:
        raise SFTPWardenError("The uv tool receipt does not identify SFTPWarden.")
    options = {}
    if environment.get("UV_CONFIG_FILE"):
        options.update(tomllib.loads(Path(environment["UV_CONFIG_FILE"]).read_text()))
    options.update(receipt.get("options", {}))
    config = temporary / "uv-options.toml"
    from sftpwarden.utils.persistence import atomic_bytes

    atomic_bytes(config, tomli_w.dumps(options).encode())
    return [
        installation["command"][0],
        "tool",
        "install",
        "--force",
        "--python",
        installation["python"],
        "--config-file",
        str(config),
        *requirements,
        main,
    ]
