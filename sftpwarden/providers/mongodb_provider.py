from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


@register_provider
class MongoDBProvider(BaseProvider):
    """MongoDB-backed user provider."""

    provider_type = ProviderType.MONGODB

    @classmethod
    def empty_text(cls) -> str:
        """Return an empty document placeholder for MongoDB providers.

        Returns
        -------
        str
            Empty string because MongoDB providers do not use seed files.
        """
        return ""

    def read(self) -> ProviderUsers:
        """Read users from MongoDB.

        Returns
        -------
        ProviderUsers
            Users loaded from MongoDB.
        """
        collection = self._collection()
        documents = list(collection.find({}, {"_id": False}).sort("username", 1))
        return ProviderUsers(users=[user_from_mongodb_document(document) for document in documents])

    def write(self, users: ProviderUsers) -> None:
        """Replace MongoDB users with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        collection = self._collection()
        self.ensure_collection()
        usernames = [user.username for user in users.users]
        for user in users.users:
            document = mongodb_document_from_user(user)
            collection.replace_one({"_id": user.username}, document, upsert=True)
        if usernames:
            collection.delete_many({"_id": {"$nin": usernames}})
        else:
            collection.delete_many({})

    def upsert_user(self, user: SFTPUser) -> None:
        """Create or update one MongoDB user document.

        Parameters
        ----------
        user
            User to persist.
        """
        collection = self._collection()
        self.ensure_collection()
        collection.replace_one(
            {"_id": user.username},
            mongodb_document_from_user(user),
            upsert=True,
        )

    def remove_user(self, username: str) -> None:
        """Remove one MongoDB user document.

        Parameters
        ----------
        username
            Username to remove.
        """
        result = self._collection().delete_one({"_id": username})
        if getattr(result, "deleted_count", 0) == 0:
            raise ProviderError(f"Unknown user: {username}", suggestion="Run `sftpwarden users`.")

    def table_exists(self) -> bool:
        """Return whether the configured MongoDB collection exists.

        Returns
        -------
        bool
            ``True`` when the collection exists.
        """
        database = self._database()
        return self.config.collection in database.list_collection_names()

    def create_table(self) -> None:
        """Create the configured MongoDB collection and username index."""
        database = self._database()
        if self.config.collection not in database.list_collection_names():
            database.create_collection(self.config.collection)
        database[self.config.collection].create_index("username", unique=True)

    def ensure_collection(self) -> None:
        """Ensure the configured MongoDB collection and index exist."""
        self.create_table()

    def _database(self) -> Any:
        if not self.config.dsn:
            raise ProviderError("MongoDB provider requires dsn.")
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise ProviderError(
                "MongoDB provider requires the mongodb optional dependency.",
                suggestion='Install SFTPWarden with "sftpwarden[mongodb]".',
            ) from exc
        dsn = os.path.expandvars(self.config.dsn)
        database_name = mongodb_database_name(dsn)
        return MongoClient(dsn)[database_name]

    def _collection(self) -> Any:
        return self._database()[self.config.collection]


def mongodb_database_name(dsn: str) -> str:
    """Return the database name from a MongoDB DSN.

    Parameters
    ----------
    dsn
        MongoDB connection URL.

    Returns
    -------
    str
        Database name.
    """
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise ProviderError("MongoDB DSN must use mongodb:// or mongodb+srv://.")
    database = parsed.path.lstrip("/").split("/", 1)[0]
    if not database:
        raise ProviderError("MongoDB DSN must include a database name.")
    return database


def mongodb_document_from_user(user: SFTPUser) -> dict[str, Any]:
    """Convert a user to a MongoDB document.

    Parameters
    ----------
    user
        User to persist.

    Returns
    -------
    dict[str, Any]
        MongoDB document.
    """
    document = user.model_dump(mode="json", exclude_none=True)
    document["_id"] = user.username
    document["username"] = user.username
    return document


def user_from_mongodb_document(document: dict[str, Any]) -> SFTPUser:
    """Convert a MongoDB document to an SFTP user.

    Parameters
    ----------
    document
        MongoDB user document.

    Returns
    -------
    SFTPUser
        Validated SFTP user.
    """
    data = dict(document)
    data.pop("_id", None)
    return SFTPUser.model_validate(data)
