from __future__ import annotations

import csv

from pydantic import ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import FileProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.files import chmod_private

CSV_FIELDNAMES = [
    "username",
    "public_keys",
    "password_hash",
    "uid",
    "gid",
    "upload_dir",
    "comment",
    "disabled",
]


@register_provider
class CSVProvider(FileProvider):
    provider_type = ProviderType.CSV

    @classmethod
    def empty_text(cls) -> str:
        return ",".join(CSV_FIELDNAMES) + "\n"

    def read(self) -> ProviderUsers:
        path = self.ensure_exists()
        users: list[SFTPUser] = []
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                public_keys = [
                    key.strip()
                    for key in (row.get("public_keys") or "").splitlines()
                    if key.strip()
                ]
                users.append(
                    SFTPUser(
                        username=row["username"],
                        public_keys=public_keys,
                        password_hash=row.get("password_hash") or None,
                        uid=int(row["uid"]) if row.get("uid") else None,
                        gid=int(row["gid"]) if row.get("gid") else None,
                        upload_dir=row.get("upload_dir") or "upload",
                        comment=row.get("comment") or None,
                        disabled=(row.get("disabled") or "").lower() in {"1", "true", "yes"},
                    )
                )
        try:
            return ProviderUsers(users=users)
        except ValidationError as exc:
            raise ProviderError(f"Invalid CSV provider file: {path}: {exc}") from exc

    def write(self, users: ProviderUsers) -> None:
        path = self.ensure_parent_dir()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for user in users.users:
                row = user.model_dump(mode="json", exclude_none=True)
                row["public_keys"] = "\n".join(user.public_keys)
                writer.writerow(row)
        chmod_private(path)
