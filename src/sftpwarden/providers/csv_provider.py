from __future__ import annotations

import csv

from pydantic import ValidationError

from sftpwarden.config import ProviderType
from sftpwarden.utils.errors import ProviderError
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers, SFTPUser

CSV_FIELDNAMES = [
    "username",
    "public_keys",
    "password_hash",
    "uid",
    "gid",
    "upload_dir",
    "disabled",
]


@register_provider
class CSVProvider(BaseProvider):
    provider_type = ProviderType.CSV

    @classmethod
    def empty_text(cls) -> str:
        return ",".join(CSV_FIELDNAMES) + "\n"

    def read(self) -> ProviderUsers:
        if self.path is None:
            raise ProviderError("CSV provider requires a path.")
        if not self.path.exists():
            raise ProviderError(
                f"Provider file not found: {self.path}",
                suggestion="Create it or run `sftpwarden init`.",
            )
        users: list[SFTPUser] = []
        with self.path.open(newline="", encoding="utf-8") as handle:
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
                        disabled=(row.get("disabled") or "").lower() in {"1", "true", "yes"},
                    )
                )
        try:
            return ProviderUsers(users=users)
        except ValidationError as exc:
            raise ProviderError(f"Invalid CSV provider file: {self.path}: {exc}") from exc

    def write(self, users: ProviderUsers) -> None:
        if self.path is None:
            raise ProviderError("CSV provider requires a path.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for user in users.users:
                row = user.model_dump(mode="json", exclude_none=True)
                row["public_keys"] = "\n".join(user.public_keys)
                writer.writerow(row)
