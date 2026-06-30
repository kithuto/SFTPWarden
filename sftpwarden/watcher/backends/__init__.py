from __future__ import annotations

from sftpwarden.watcher.backends.docker import (  # noqa: F401
    DEFAULT_LOCAL_WATCHER_IMAGE,
    GHCR_WATCHER_IMAGE_REPOSITORY,
    LOCAL_WATCHER_DOCKERFILE,
    SOURCE_ROOT,
    DockerComposeMount,
    DockerWatcher,
    docker_watcher_compose_path,
    docker_watcher_remote_contexts,
    docker_watcher_ssh_volumes,
    render_docker_watcher_compose,
    watcher_image_reference,
)
from sftpwarden.watcher.backends.launchd import (  # noqa: F401
    LAUNCHD_LABEL,
    LaunchdWatcher,
    launchd_plist_path,
    launchd_target_path,
    render_launchd_plist,
)
from sftpwarden.watcher.backends.openrc import (  # noqa: F401
    OpenRCWatcher,
    openrc_script_path,
    render_openrc_script,
)
from sftpwarden.watcher.backends.runit import (  # noqa: F401
    RunitWatcher,
    render_runit_script,
    runit_script_path,
)
from sftpwarden.watcher.backends.supervisord import (  # noqa: F401
    SupervisordWatcher,
    render_supervisord_config,
    supervisor_config_target,
    supervisord_config_path,
)
from sftpwarden.watcher.backends.systemd import (  # noqa: F401
    SystemdWatcher,
    render_systemd_unit,
    systemd_unit_path,
)
from sftpwarden.watcher.backends.windows_task import (  # noqa: F401
    WINDOWS_TASK_NAME,
    WindowsTaskWatcher,
    render_windows_script,
    windows_script_path,
)
