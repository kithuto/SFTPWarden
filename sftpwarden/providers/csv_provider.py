from __future__ import annotations

import csv

from pydantic import ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import FileProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.users.schemas import (
    UserSchemaVersion,
    detect_csv_schema,
    user_schema,
)
from sftpwarden.users.schemas.v1 import CSV_V1_FIELDNAMES
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import chmod_private

CSV_FIELDNAMES = CSV_V1_FIELDNAMES


@register_provider
class CSVProvider(FileProvider):
    """CSV-backed user provider."""

    provider_type = ProviderType.CSV

    @classmethod
    def empty_text(cls) -> str:
        """Return an empty CSV provider document.

        Returns
        -------
        str
            CSV header row.
        """
        return ",".join(CSV_V1_FIELDNAMES) + "\n"

    @classmethod
    def empty_text_for_schema(cls, schema_version: UserSchemaVersion) -> str:
        """Return an empty CSV provider document for a schema version."""
        return ",".join(user_schema(schema_version).csv_fieldnames()) + "\n"

    def read(self) -> ProviderUsers:
        """Read users from a CSV provider file.

        Returns
        -------
        ProviderUsers
            Parsed provider users.

        Raises
        ------
        ProviderError
            Raised when the file is missing or invalid.
        """
        path = self.ensure_exists()
        users: list[SFTPUser] = []
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                schema = detect_csv_schema(list(reader.fieldnames or []), fallback_schema=1)
                for row in reader:
                    users.append(schema.user_from_csv_row(row))
            return ProviderUsers(schema_version=schema.version, users=users)
        except ValidationError as exc:
            raise ProviderError(f"Invalid CSV provider file: {path}: {exc}") from exc

    def write(self, users: ProviderUsers) -> None:
        """Write users to a CSV provider file.

        Parameters
        ----------
        users
            Users to persist.
        """
        path = self.ensure_parent_dir()
        schema = user_schema(users.schema_version)
        fieldnames = schema.csv_fieldnames()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for user in users.users:
                writer.writerow(schema.csv_row_from_user(user))
        chmod_private(path)
