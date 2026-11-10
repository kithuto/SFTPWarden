import pytest

from sftpwarden.providers.sql import upsert_sql_user
from sftpwarden.users import SFTPUser, SFTPUserKey


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_t10_sql_single_user_upsert_never_clears_other_users_keys(dialect: str) -> None:
    statements = []

    class Cursor:
        def execute(self, statement, params=None):
            statements.append((statement, params))

        def executemany(self, statement, rows):
            statements.append((statement, rows))

    upsert_sql_user(
        Cursor(),
        "sftp_users",
        SFTPUser(
            username="alice",
            keys=[
                SFTPUserKey(
                    name="main",
                    public_key=(
                        "ssh-ed25519 "
                        "AAAAC3NzaC1lZDI1NTE5AAAAIFMsDFmvcbuEKKQ2nRx6M4jxyJuS9KoOesN7GyrXw+d+"
                    ),
                )
            ],
        ),
        dialect=dialect,
        schema_version=2,
    )
    deletes = [
        (statement, params) for statement, params in statements if statement.startswith("delete")
    ]
    assert deletes == [("delete from sftp_user_keys where username = %s", ["alice"])]
