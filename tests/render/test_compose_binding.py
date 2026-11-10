import pytest

from sftpwarden.config import default_project_config
from sftpwarden.render.compose import compose_model


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "::1", "::"])
def test_t16_compose_publishes_only_the_configured_host(host: str) -> None:
    config = default_project_config("binding")
    config.server.host = host
    config.server.port = 2244
    service = compose_model(config)["services"]["sftpwarden"]
    assert service["ports"] == [
        {"target": 22, "published": "2244", "host_ip": host, "protocol": "tcp"}
    ]


def test_compose_mounts_configured_runtime_directories() -> None:
    config = default_project_config("custom")
    config.server.data_dir = "/srv/sftp/data"
    config.server.state_dir = "/srv/sftp/state"
    config.server.host_keys_dir = "/srv/sftp/identity"
    service = compose_model(config)["services"]["sftpwarden"]
    assert "./data:/srv/sftp/data" in service["volumes"]
    assert "./state:/srv/sftp/state" in service["volumes"]
    assert "./host_keys:/srv/sftp/identity" in service["volumes"]
    assert service["environment"]["SFTPWARDEN_HOST_KEYS_DIR"] == "/srv/sftp/identity"
