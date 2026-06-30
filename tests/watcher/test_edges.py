from __future__ import annotations

from pathlib import Path

import pytest

import sftpwarden.utils.platform as platform_utils
import sftpwarden.watcher.backends.docker as docker_backend
import sftpwarden.watcher.backends.launchd as launchd_backend
import sftpwarden.watcher.backends.openrc as openrc_backend
import sftpwarden.watcher.backends.runit as runit_backend
import sftpwarden.watcher.backends.supervisord as supervisord_backend
import sftpwarden.watcher.backends.systemd as systemd_backend
import sftpwarden.watcher.backends.windows_task as windows_backend
import sftpwarden.watcher.core as watcher_core
from sftpwarden.config import ProviderType, RemoteStorage
from sftpwarden.contexts import (
    ContextRegistry,
    ContextType,
    local_context,
    remote_context,
    save_registry,
)
from sftpwarden.utils.errors import ContextError
from sftpwarden.watcher.base import BaseWatcher, WatcherInstallMode
from sftpwarden.watcher.registry import watcher_class


def test_base_watcher_write_creates_parent_directory(tmp_path: Path) -> None:
    class ExampleWatcher(BaseWatcher):
        mode = WatcherInstallMode.SYSTEMD

        @classmethod
        def is_supported(cls) -> bool:
            return True

        @classmethod
        def path(cls) -> Path:
            return tmp_path / "nested" / "watcher.conf"

        @classmethod
        def render(cls, *, image: str | None = None) -> str:
            return f"rendered image={image}\n"

        @classmethod
        def commands(cls, *, image: str | None = None) -> list[list[str]]:
            return [["activate"]]

        @classmethod
        def uninstall_commands(cls, *, path: Path | None = None) -> list[list[str]]:
            return [["deactivate"]]

    path = ExampleWatcher.write(image="custom")

    assert path == tmp_path / "nested" / "watcher.conf"
    assert path.read_text(encoding="utf-8") == "rendered image=custom\n"


def test_base_watcher_abstract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        BaseWatcher.is_supported()
    with pytest.raises(NotImplementedError):
        BaseWatcher.path()
    with pytest.raises(NotImplementedError):
        BaseWatcher.render()
    with pytest.raises(NotImplementedError):
        BaseWatcher.commands()
    with pytest.raises(NotImplementedError):
        BaseWatcher.uninstall_commands()


def test_watcher_registry_reports_unregistered_mode() -> None:
    with pytest.raises(ContextError, match="Watcher backend is not registered: auto"):
        watcher_class(WatcherInstallMode.AUTO)


def test_platform_helpers_cover_resolution_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_utils.shutil, "which", lambda name: f"/bin/{name}")
    assert platform_utils.executable_command("sftpwarden") == ["/bin/sftpwarden"]

    monkeypatch.setattr(platform_utils.shutil, "which", lambda _name: None)
    assert platform_utils.executable_command("sftpwarden", env_fallback=True) == [
        "/usr/bin/env",
        "sftpwarden",
    ]
    assert platform_utils.executable_command("sftpwarden") == ["sftpwarden"]

    monkeypatch.setenv("SUDO_USER", "deploy")
    assert platform_utils.current_username() == "deploy"


def test_launchd_backend_renders_commands_and_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(launchd_backend, "executable_path", lambda _name: "/opt/bin/sftp&warden")
    monkeypatch.setattr(launchd_backend, "system_is", lambda name: name == "Darwin")
    monkeypatch.setattr(launchd_backend.shutil, "which", lambda name: f"/bin/{name}")

    rendered = launchd_backend.LaunchdWatcher.render()
    commands = launchd_backend.LaunchdWatcher.commands()
    uninstall = launchd_backend.LaunchdWatcher.uninstall_commands()

    assert launchd_backend.LaunchdWatcher.is_supported()
    assert "/opt/bin/sftp&amp;warden" in rendered
    assert commands[-1][:2] == ["launchctl", "load"]
    assert uninstall[0][:2] == ["launchctl", "unload"]


def test_linux_native_backend_support_and_uninstall_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))

    monkeypatch.setattr(openrc_backend, "system_is", lambda name: name == "Linux")
    monkeypatch.setattr(openrc_backend.shutil, "which", lambda name: f"/bin/{name}")
    assert openrc_backend.OpenRCWatcher.is_supported()
    assert openrc_backend.OpenRCWatcher.uninstall_commands()[0][:3] == [
        "sudo",
        "rc-service",
        "sftpwarden-watch",
    ]

    monkeypatch.setattr(systemd_backend, "system_is", lambda name: name == "Linux")
    monkeypatch.setattr(systemd_backend.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        systemd_backend.Path,
        "exists",
        lambda self: str(self).replace("\\", "/") == "/run/systemd/system",
    )
    assert systemd_backend.SystemdWatcher.is_supported()

    monkeypatch.setattr(windows_backend, "system_is", lambda name: name == "Windows")
    monkeypatch.setattr(windows_backend.shutil, "which", lambda name: f"C:/Windows/{name}")
    assert windows_backend.WindowsTaskWatcher.is_supported()
    assert windows_backend.WindowsTaskWatcher.uninstall_commands() == [
        ["schtasks", "/Delete", "/TN", "SFTPWarden Watcher", "/F"]
    ]


def test_runit_backend_render_support_and_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runit_backend, "current_username", lambda: "deploy")
    monkeypatch.setattr(
        runit_backend,
        "executable_command",
        lambda _name, *, env_fallback=False: ["/usr/bin/env", "sftpwarden"],
    )
    monkeypatch.setattr(runit_backend, "system_is", lambda name: name == "Linux")
    monkeypatch.setattr(runit_backend.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        runit_backend.Path,
        "exists",
        lambda self: str(self).replace("\\", "/") in {"/etc/sv", "/var/service"},
    )

    rendered = runit_backend.RunitWatcher.render()

    assert runit_backend.RunitWatcher.is_supported()
    assert "exec chpst -u deploy /usr/bin/env sftpwarden watch" in rendered
    assert runit_backend.RunitWatcher.uninstall_commands()[-1] == [
        "sudo",
        "rm",
        "-rf",
        "/etc/sv/sftpwarden-watch",
    ]


def test_supervisord_backend_target_render_and_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(supervisord_backend, "current_username", lambda: "deploy")
    monkeypatch.setattr(
        supervisord_backend,
        "executable_command",
        lambda _name, *, env_fallback=False: ["/usr/bin/env", "sftpwarden"],
    )
    monkeypatch.setattr(supervisord_backend, "system_is", lambda name: name == "Linux")
    monkeypatch.setattr(supervisord_backend.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        supervisord_backend.Path,
        "exists",
        lambda self: str(self).replace("\\", "/") == "/etc/supervisord.d",
    )

    rendered = supervisord_backend.SupervisordWatcher.render()

    assert supervisord_backend.supervisor_config_target().replace("\\", "/") == (
        "/etc/supervisord.d/sftpwarden-watch.conf"
    )
    assert supervisord_backend.SupervisordWatcher.is_supported()
    assert "command=/usr/bin/env sftpwarden watch" in rendered
    assert supervisord_backend.SupervisordWatcher.commands()[0][-1].endswith(
        "sftpwarden-watch.conf"
    )
    assert supervisord_backend.SupervisordWatcher.uninstall_commands()[0] == [
        "sudo",
        "supervisorctl",
        "stop",
        "sftpwarden-watch",
    ]


def test_docker_backend_path_rewrites_and_unique_mounts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside_config = tmp_path / "outside.yaml"
    entry = local_context("Prod Context!", project, ProviderType.YAML).model_copy(
        update={"config": str(outside_config)}
    )
    duplicate = docker_backend.docker_bind_mount(project, "/workspace/project")

    assert docker_backend.docker_context_config_path(entry).endswith("/sftpwarden.yaml")
    assert docker_backend.docker_context_key_path(entry).endswith("/identity")
    assert docker_backend.unique_mounts([duplicate, duplicate]) == [duplicate]


def test_docker_backend_ignores_incomplete_contexts_and_rewrites_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    key = tmp_path / "deploy_key"
    key.write_text("private", encoding="utf-8")
    good = remote_context(
        name="prod",
        provider=ProviderType.YAML,
        remote_url="deploy@example.com:/opt/sftpwarden",
        local_root=project,
        remote_root="/opt/sftpwarden",
        remote_only=False,
        ssh_key=str(key),
        critical=True,
    )
    incomplete = good.model_copy(update={"root": ""})
    save_registry(ContextRegistry(default="prod", contexts={"prod": good}))
    monkeypatch.setattr(
        docker_backend,
        "docker_watcher_remote_contexts",
        lambda: [incomplete, good],
    )

    registry = docker_backend.docker_watcher_container_registry()

    assert set(registry.contexts) == {"prod"}
    assert registry.contexts["prod"].root.startswith("/workspace/prod-")
    assert registry.contexts["prod"].remote is not None
    assert registry.contexts["prod"].remote.ssh_key.endswith("/identity")


def test_docker_backend_ssh_volumes_skip_context_without_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = local_context("dev", tmp_path / "dev", ProviderType.YAML).model_copy(
        update={
            "type": ContextType.REMOTE,
            "storage": RemoteStorage.LOCAL_SYNC,
            "remote": None,
        }
    )
    monkeypatch.setattr(docker_backend, "docker_watcher_remote_contexts", lambda: [entry])

    assert docker_backend.docker_watcher_ssh_volumes() == []


def test_docker_watcher_support_uninstall_and_core_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(docker_backend.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(watcher_core.watcher_backends, "docker_watcher_remote_contexts", lambda: [])
    monkeypatch.setattr(watcher_core.watcher_backends, "docker_watcher_ssh_volumes", lambda: [])

    assert docker_backend.DockerWatcher.is_supported()
    assert docker_backend.DockerWatcher.uninstall_commands(path=tmp_path / "compose.yml") == [
        ["docker", "compose", "-f", str(tmp_path / "compose.yml"), "down"]
    ]
    assert watcher_core.watcher_image_reference(image="custom").image == "custom"
    assert watcher_core.docker_watcher_remote_contexts() == []
    assert watcher_core.docker_watcher_ssh_volumes() == []
    assert watcher_core.docker_watcher_compose_path().name == "docker-compose.yml"


def test_watcher_core_remaining_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        watcher_core,
        "native_watcher_classes",
        lambda: [type("Unsupported", (), {"is_supported": classmethod(lambda cls: False)})],
    )
    assert watcher_core.detect_native_watcher_mode() is None

    with pytest.raises(ContextError, match="watcher --image"):
        watcher_core.install_watcher(mode="systemd", image="not-for-systemd", yes=True)
    with pytest.raises(ContextError, match="watcher --image"):
        watcher_core.watcher_install_plan(WatcherInstallMode.SYSTEMD, image="not-for-systemd")

    monkeypatch.setattr(
        watcher_core.watcher_class(WatcherInstallMode.SYSTEMD),
        "is_supported",
        classmethod(lambda cls: False),
    )
    with pytest.raises(ContextError, match="not supported"):
        watcher_core.install_watcher(mode="systemd", yes=True, activate=True)


def test_watcher_replacement_dry_run_and_broken_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sftpwarden.config.global_config import load_global_config, save_global_config

    monkeypatch.setenv("SFTPWARDEN_HOME", str(tmp_path / "home"))
    watcher_core.install_watcher(mode="systemd", yes=True, activate=False)

    dry_run = watcher_core.install_watcher(mode="docker", yes=True, dry_run=True)

    assert "Would replace existing systemd watcher" in dry_run
    config = load_global_config()
    config.watcher.installed = True
    config.watcher.mode = None
    save_global_config(config)
    with pytest.raises(ContextError, match="missing its mode"):
        watcher_core.uninstall_watcher()
