from __future__ import annotations

import pytest

from sftpwarden.config import ProviderType
from sftpwarden.utils.collections import unique_items
from sftpwarden.utils.console import print_warning
from sftpwarden.utils.dsn import sql_default_port, sql_dsn_scheme

TEST_HASH = "$6$rounds=500000$saltstring$hashvalue"


def test_collection_and_dsn_utilities_are_stable() -> None:
    assert unique_items(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]
    assert sql_dsn_scheme(ProviderType.MYSQL) == "mysql"
    assert sql_dsn_scheme(ProviderType.MARIADB) == "mariadb"
    assert sql_dsn_scheme(ProviderType.POSTGRESQL) == "postgresql"
    assert sql_default_port(ProviderType.MYSQL) == 3306
    assert sql_default_port(ProviderType.MARIADB) == 3306
    assert sql_default_port(ProviderType.POSTGRESQL) == 5432


def test_warning_output_uses_standard_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    print_warning("check this")

    assert "Warning" in capsys.readouterr().out
