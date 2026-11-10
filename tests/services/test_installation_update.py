"""Installer ownership, candidate preparation and private handoff failure contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.services import installation, upgrade_worker
from sftpwarden.utils.errors import SFTPWardenError


@pytest.fixture
def package_environment(tmp_path, monkeypatch):
    prefix = tmp_path / "tools with spaces" / "sftpwarden"
    prefix.mkdir(parents=True)
    (prefix / "pyvenv.cfg").touch()
    distribution = {"INSTALLER": "pip"}
    monkeypatch.setattr(installation, "_pyproject_path", lambda: None)
    monkeypatch.setattr(
        installation,
        "sys",
        SimpleNamespace(
            prefix=str(prefix), base_prefix="/system", executable=str(prefix / "bin/python")
        ),
    )
    monkeypatch.setattr(
        installation.metadata,
        "distribution",
        lambda _: SimpleNamespace(read_text=distribution.get, locate_file=lambda _: prefix),
    )
    monkeypatch.setattr(installation, "installed_drivers", lambda: ["PyMySQL"])
    monkeypatch.setattr(installation.shutil, "which", lambda name: str(tmp_path / name))
    monkeypatch.setattr(
        installation.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=str(prefix.parent)),
    )
    original_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: (
            False if str(path) in {"/.dockerenv", "/run/.containerenv"} else original_exists(path)
        ),
    )
    return prefix, distribution


@pytest.mark.parametrize("manager", ["pip", "pipx", "uv"])
def test_plans_only_the_owning_manager(package_environment, manager):
    prefix, _ = package_environment
    if manager == "pipx":
        (prefix / "pipx_metadata.json").write_text(
            json.dumps({"main_package": {"package": "sftpwarden"}})
        )
    if manager == "uv":
        (prefix / "uv-receipt.toml").write_text("[tool]\n")
    plan = installation.installation_plan("1.3.2")
    assert plan.manager == manager and Path(plan.prefix) == prefix
    assert plan.python == str(prefix / "bin/python")
    assert plan.drivers == ["PyMySQL"]
    if manager != "pipx":
        assert plan.command[-1] == "sftpwarden[mysql]==1.3.2"
    assert not list(prefix.glob("**/management.json"))


@pytest.mark.parametrize(
    "reason",
    [
        "direct",
        "system",
        "readonly",
        "unknown",
        "pipx_missing",
        "uv_missing",
        "pipx_suffix",
        "pipx_pinned",
    ],
)
def test_unsupported_owners_fail_before_mutation(package_environment, monkeypatch, reason):
    prefix, distribution = package_environment
    if reason == "direct":
        distribution["direct_url.json"] = '{"url":"https://private.invalid/package"}'
    elif reason == "system":
        installation.sys.base_prefix = str(prefix)
    elif reason == "readonly":
        monkeypatch.setattr(installation.os, "access", lambda *a: False)
    elif reason == "unknown":
        distribution["INSTALLER"] = "custom"
    elif reason.startswith("pipx"):
        data = {
            "package": "sftpwarden",
            "suffix": "-custom" if reason == "pipx_suffix" else "",
            "pinned": reason == "pipx_pinned",
        }
        (prefix / "pipx_metadata.json").write_text(json.dumps({"main_package": data}))
        if reason == "pipx_missing":
            monkeypatch.setattr(installation.shutil, "which", lambda _: None)
    elif reason == "uv_missing":
        (prefix / "uv-receipt.toml").write_text("[tool]\n")
        monkeypatch.setattr(installation.shutil, "which", lambda _: None)
    with pytest.raises(SFTPWardenError):
        installation.installation_plan("1.3.2")


def test_uv_advances_only_package_pin_and_preserves_options_and_extras(tmp_path):
    import tomli_w

    prefix = tmp_path / "uv tool"
    prefix.mkdir()
    receipt = {
        "tool": {
            "requirements": [
                {"name": "sftpwarden", "specifier": "==1.3.1", "extras": ["postgres"]},
                {"name": "an-extra", "specifier": ">=2"},
            ],
            "options": {"index-url": "https://private.invalid/simple"},
        }
    }
    content = tomli_w.dumps(receipt)
    (prefix / "uv-receipt.toml").write_text(content)
    plan = dict(
        manager="uv",
        prefix=str(prefix),
        python="own-python",
        command=["own-uv"],
        drivers=["PyMySQL"],
    )
    command = installation.installer_command(plan, "1.3.2", tmp_path, {})
    assert command[-1] == "sftpwarden[mysql,postgres]==1.3.2"
    assert command[command.index("--with") + 1] == "an-extra>=2"
    assert command[command.index("--python") + 1] == "own-python"
    assert "https://private.invalid/simple" in (tmp_path / "uv-options.toml").read_text()
    assert (prefix / "uv-receipt.toml").read_text() == content


@pytest.mark.parametrize("failure", ["preparation", "verification", None])
def test_pip_prepares_private_artifacts_before_touching_owner(tmp_path, monkeypatch, failure):
    calls = []
    plan = dict(
        manager="pip",
        prefix=str(tmp_path / "owner"),
        python="owner-python",
        drivers=[],
        command=["owner-python", "install"],
    )

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=int(
                (len(calls) == 1 and failure == "preparation")
                or (len(calls) == 2 and failure == "verification")
            )
        )

    monkeypatch.setattr(installation.subprocess, "run", run)
    if failure:
        with pytest.raises(SFTPWardenError):
            installation.prepare_installation(plan, "1.3.2", tmp_path, {})
    else:
        installation.prepare_installation(plan, "1.3.2", tmp_path, {})
    assert calls[0][calls[0].index("--target") + 1] == str(tmp_path / "candidate")
    assert calls[0][-1] == "sftpwarden==1.3.2"
    assert plan["command"] not in calls


@pytest.mark.parametrize("failure", ["installer", "verification", None])
def test_worker_verifies_fresh_process_before_recording_success(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    receipt = tmp_path / "management.json"
    plan = {
        "cli": {"target": "1.3.2"},
        "installation": {
            "manager": "pip",
            "python": "owner-python",
            "prefix": str(tmp_path / "owner"),
            "drivers": ["PyMySQL"],
            "command": ["installer"],
        },
    }
    receipt.write_text(json.dumps({"plan": plan}))
    calls = []
    monkeypatch.setattr(installation, "prepare_installation", lambda *a: calls.append("prepared"))
    monkeypatch.setattr(upgrade_worker, "old_cli_step", lambda *a: calls.append("paused"))

    def run(command, **kwargs):
        operation = "installer" if command == ["installer"] else "verification"
        calls.append(operation)
        if operation == "verification":
            assert command[:3] == ["owner-python", "-I", "-c"]
            assert "PyMySQL" in command[-1] and "finish_management_update" in command[-1]
        else:
            assert "file:" in kwargs["env"]["PIP_CONSTRAINT"]
        return SimpleNamespace(
            returncode=int(operation == failure), stdout='{"cli":{"applied":"1.3.2"}}'
        )

    monkeypatch.setattr(upgrade_worker.subprocess, "run", run)
    monkeypatch.setattr(upgrade_worker.sys, "argv", ["worker", str(receipt)])
    assert upgrade_worker.main() == (1 if failure else 0)
    record = json.loads(receipt.read_text())
    assert record["status"] == ("incomplete" if failure else "complete")
    assert record["plan"] == plan
    assert calls[:3] == ["prepared", "paused", "installer"]


def test_installed_provider_drivers_are_retained_without_assuming_all_are_present(monkeypatch):
    def version(name):
        if name == "psycopg":
            raise installation.metadata.PackageNotFoundError(name)
        return "1.0"

    monkeypatch.setattr(installation.metadata, "version", version)
    assert installation.installed_drivers() == ["PyMySQL", "psycopg-binary", "pymongo"]


@pytest.mark.parametrize("owner", ["pipx", "uv"])
def test_candidate_preparation_preserves_manager_options_in_an_isolated_location(
    tmp_path, monkeypatch, owner
):
    prefix = tmp_path / "owner"
    prefix.mkdir()
    if owner == "pipx":
        (prefix / "pipx_metadata.json").write_text(
            json.dumps(
                {
                    "main_package": {
                        "package_or_url": "sftpwarden[postgres]",
                        "pip_args": ["--no-cache-dir"],
                    }
                }
            )
        )
    else:
        (prefix / "uv-receipt.toml").write_text(
            '[tool]\nrequirements = [{name="sftpwarden", extras=["postgres"]}]\n'
        )
    plan = dict(
        manager=owner,
        prefix=str(prefix),
        python="owner-python",
        drivers=["PyMySQL"],
        command=["owner-manager"],
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installation.subprocess, "run", run)
    installation.prepare_installation(plan, "1.3.2", tmp_path, {})
    assert calls[0][0][-1] == "sftpwarden[mysql,postgres]==1.3.2"
    if owner == "pipx":
        assert "--no-cache-dir" in calls[0][0] and "--target" in calls[0][0]
    else:
        assert calls[0][1]["env"]["UV_TOOL_DIR"] == str(tmp_path / "tools")
    assert calls[1][0][1:3] == ["-I", "-c"]
    assert not (prefix / "candidate").exists()


@pytest.mark.parametrize("failed", [False, True])
def test_short_lived_old_cli_checks_must_finish_before_installation(monkeypatch, failed):
    calls = []
    monkeypatch.setattr(
        upgrade_worker.subprocess,
        "run",
        lambda cmd, **kw: calls.append((cmd, kw)) or SimpleNamespace(returncode=int(failed)),
    )
    if failed:
        with pytest.raises(RuntimeError, match="Pre-install"):
            upgrade_worker.old_cli_step({"python": "old-python"}, "check()")
    else:
        upgrade_worker.old_cli_step({"python": "old-python"}, "check()")
    assert calls[0][0] == ["old-python", "-I", "-c", "check()"]
    assert calls[0][1]["timeout"] == 180
