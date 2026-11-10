import json
import os
import subprocess
import sys
import time

import pytest

from sftpwarden.cli_commands.config import update_project_config_data
from sftpwarden.config import ProviderType, default_project_config, load_config, write_config
from sftpwarden.contexts import ContextRegistry, local_context, save_registry
from sftpwarden.utils.errors import SFTPWardenError


@pytest.mark.parametrize("same_field", [False, True])
def test_concurrent_config_processes_preserve_other_fields_or_report_conflict(tmp_path, same_field):
    config_path = tmp_path / "sftpwarden.yaml"
    write_config(config_path, default_project_config("transactions"))
    worker = """
import sys, time
from pathlib import Path
from sftpwarden.config import load_config
from sftpwarden.contexts import local_context
from sftpwarden.cli_commands.config import update_project_config_data
from sftpwarden.utils.errors import SFTPWardenError
root, field, value, marker = sys.argv[1:]
root = Path(root)
config_path = root / 'sftpwarden.yaml'
config = load_config(config_path)
entry = local_context('transactions', root, config.provider.type)
data = config.model_dump(mode='json')
(root / marker).touch()
deadline = time.monotonic() + 10
while not (root / 'go').exists():
    if time.monotonic() > deadline:
        raise SystemExit(3)
    time.sleep(.01)
try:
    update_project_config_data(entry=entry, data=data, path=field, value=value,
        context_config_path=str(config_path), yes=True)
except SFTPWardenError:
    raise SystemExit(2)
"""
    processes = []
    try:
        edits = [
            ("server.port", "2301"),
            ("server.port", "2302") if same_field else ("sync.interval_seconds", "37"),
        ]
        for index, (field, value) in enumerate(edits):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", worker, str(tmp_path), field, value, f"ready-{index}"],
                    env={**os.environ, "SFTPWARDEN_HOME": str(tmp_path / "home")},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + 10
        while not all((tmp_path / f"ready-{index}").exists() for index in range(2)):
            assert all(process.poll() is None for process in processes)
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (tmp_path / "go").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode in {0, 2}, stdout + stderr
        updated = load_config(config_path)
        if same_field:
            assert sorted(process.returncode for process in processes) == [0, 2]
            assert updated.server.port in {2301, 2302}
        else:
            assert all(process.returncode == 0 for process in processes)
            assert updated.server.port == 2301
            assert updated.sync.interval_seconds == 37
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_config_rename_collision_does_not_change_project_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    source = root / "sftpwarden.yaml"
    config = default_project_config("one")
    write_config(source, config)
    one = local_context("one", root, ProviderType.YAML)
    two = local_context("two", tmp_path / "other", ProviderType.YAML)
    save_registry(ContextRegistry(default="one", contexts={"one": one, "two": two}))
    before = source.read_bytes()
    with pytest.raises(SFTPWardenError, match="already exists"):
        update_project_config_data(
            entry=one,
            data=json.loads(config.model_dump_json()),
            path="project.name",
            value="two",
            context_config_path=None,
            yes=True,
        )
    assert source.read_bytes() == before


def test_schema_command_keeps_an_unrelated_config_edit(tmp_path, monkeypatch):
    import sftpwarden.cli_commands.provider as provider_commands

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    source = tmp_path / "sftpwarden.yaml"
    config = default_project_config("schema")
    config.provider.user_schema = 1
    write_config(source, config)
    (tmp_path / "users.yaml").write_text("schema_version: 1\nusers: []\n")
    reconcile = provider_commands.reconcile_provider_schema

    def migrate_then_edit(*args, **kwargs):
        result = reconcile(*args, **kwargs)
        current = load_config(source)
        current.server.port = 2345
        write_config(source, current)
        return result

    monkeypatch.setattr(provider_commands, "reconcile_provider_schema", migrate_then_edit)
    result = provider_commands.migrate_provider_schema(
        to_schema=2,
        context=None,
        config=str(source),
        backup=True,
        yes=True,
        dry_run=False,
    )
    assert result["changed"]
    current = load_config(source)
    assert current.provider.user_schema == 2
    assert current.server.port == 2345
