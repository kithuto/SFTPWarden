from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.mutations import (
    mutation_conflict,
    prepare_user_mutation,
    require_unchanged_snapshot,
)
from sftpwarden.providers.registry import register_provider
from sftpwarden.security.environment import resolve_provider_dsn
from sftpwarden.security.provider_errors import private_database_errors
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.users.schemas import detect_mapping_schema, user_schema
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

    @private_database_errors
    def read(self) -> ProviderUsers:
        """Read users from MongoDB.

        Returns
        -------
        ProviderUsers
            Users loaded from MongoDB.
        """
        database = self._database()
        try:
            identity = self._collection_identity(database)
            collection = database[self.config.collection]
            documents = list(collection.find({}, {"_id": False}).sort("username", 1))
            if self._collection_identity(database) != identity:
                raise ProviderError(
                    "MongoDB provider collection changed during the read; retaining applied policy."
                )
            return mongodb_users_from_documents(documents, fallback_schema=self.config.user_schema)
        finally:
            database.client.close()

    def _collection_identity(self, database: Any) -> Any:
        """Distinguish an explicitly empty collection from missing/replaced provider storage."""
        cursor = database.list_collections(filter={"name": self.config.collection})
        try:
            descriptions = list(cursor)
        finally:
            cursor.close()
        if len(descriptions) != 1 or not descriptions[0].get("info", {}).get("uuid"):
            raise ProviderError(
                "MongoDB provider collection is missing or has no stable identity; "
                "retaining applied policy."
            )
        return descriptions[0]["info"]["uuid"]

    @private_database_errors
    def write(self, users: ProviderUsers) -> None:
        """Replace MongoDB users with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        self._transactional_replace(users)

    def _transactional_replace(
        self, users: ProviderUsers, *, expected: ProviderUsers | None = None
    ) -> None:
        database = self._database()
        client = database.client
        try:
            topology = client.admin.command("hello")
            if not (topology.get("setName") or topology.get("msg") == "isdbgrid"):
                raise ProviderError(
                    "MongoDB bulk replacement requires a replica set or sharded cluster.",
                    suggestion=(
                        "Standalone supports individual user edits. Configure a replica set "
                        "before import, copy, schema migration or provider restore."
                    ),
                )
            collection = database[self.config.collection]
            with client.start_session() as session, session.start_transaction():
                if expected is not None:
                    documents = list(
                        collection.find({}, {"_id": False}, session=session).sort("username", 1)
                    )
                    current = mongodb_users_from_documents(
                        documents, fallback_schema=self.config.user_schema
                    )
                    require_unchanged_snapshot(current, expected)
                for user in users.users:
                    collection.replace_one(
                        {"_id": user.username},
                        mongodb_document_from_user(user, schema_version=users.schema_version),
                        upsert=True,
                        session=session,
                    )
                collection.delete_many(
                    {"_id": {"$nin": [user.username for user in users.users]}}, session=session
                )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "MongoDB bulk write failed or conflicted; reload before retrying."
            ) from exc
        finally:
            client.close()

    @private_database_errors
    def replace_if_unchanged(self, expected: ProviderUsers, replacement: ProviderUsers) -> None:
        """Compare and replace the full collection within one supported transaction."""
        self._transactional_replace(replacement, expected=expected)

    @private_database_errors
    def mutate_user(
        self,
        expected: ProviderUsers,
        username: str,
        replacement: SFTPUser | None,
        *,
        migrate_schema: bool = False,
    ) -> None:
        """Use whole-document compare-and-swap on standalone and replica-set deployments."""
        database = self._database()
        client = database.client
        try:
            collection = database[self.config.collection]
            document = collection.find_one({"_id": username})
            current = user_schema(expected.schema_version).users_from_mapping(
                {
                    "users": [mongodb_document_to_user_mapping(document)]
                    if document is not None
                    else []
                }
            )
            prepare_user_mutation(
                current, expected, username, replacement, migrate_schema=migrate_schema
            )
            if document is None:
                if replacement is None:
                    raise mutation_conflict()
                collection.insert_one(
                    mongodb_document_from_user(replacement, schema_version=expected.schema_version)
                )
            else:
                condition = {"_id": username, "$expr": {"$eq": ["$$ROOT", {"$literal": document}]}}
                if replacement is None:
                    result = collection.delete_one(condition)
                    count = result.deleted_count
                else:
                    result = collection.replace_one(
                        condition,
                        mongodb_document_from_user(
                            replacement, schema_version=expected.schema_version
                        ),
                    )
                    count = result.matched_count
                if count != 1:
                    raise mutation_conflict()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "MongoDB mutation failed or conflicted; reload before retrying."
            ) from exc
        finally:
            client.close()

    @private_database_errors
    def upsert_user(self, user: SFTPUser) -> None:
        """Create or update one MongoDB user document.

        Parameters
        ----------
        user
            User to persist.
        """
        database = self._database()
        try:
            self._ensure_collection(database)
            database[self.config.collection].replace_one(
                {"_id": user.username},
                mongodb_document_from_user(user, schema_version=self.config.user_schema),
                upsert=True,
            )
        finally:
            database.client.close()

    @private_database_errors
    def remove_user(self, username: str) -> None:
        """Remove one MongoDB user document.

        Parameters
        ----------
        username
            Username to remove.
        """
        database = self._database()
        try:
            result = database[self.config.collection].delete_one({"_id": username})
            if getattr(result, "deleted_count", 0) == 0:
                raise ProviderError(
                    f"Unknown user: {username}", suggestion="Run `sftpwarden users`."
                )
        finally:
            database.client.close()

    @private_database_errors
    def table_exists(self) -> bool:
        """Return whether the configured MongoDB collection exists.

        Returns
        -------
        bool
            ``True`` when the collection exists.
        """
        database = self._database()
        try:
            return self.config.collection in database.list_collection_names()
        finally:
            database.client.close()

    @private_database_errors
    def create_table(self) -> None:
        """Create the configured MongoDB collection and username index."""
        database = self._database()
        try:
            self._ensure_collection(database)
        finally:
            database.client.close()

    def _ensure_collection(self, database: Any) -> None:
        if self.config.collection not in database.list_collection_names():
            database.create_collection(self.config.collection)
        database[self.config.collection].create_index("username", unique=True)

    def ensure_collection(self) -> None:
        """Ensure the configured MongoDB collection and index exist."""
        self.create_table()

    def _database(self) -> Any:
        """Return the configured MongoDB database handle."""
        if not self.config.dsn:
            raise ProviderError("MongoDB provider requires dsn.")
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise ProviderError(
                "MongoDB provider requires the mongodb optional dependency.",
                suggestion='Install SFTPWarden with "sftpwarden[mongodb]".',
            ) from exc
        dsn = resolve_provider_dsn(self.config.dsn)
        database_name = mongodb_database_name(dsn)
        options: dict[str, Any] = mongodb_timeout_options(dsn)
        client: MongoClient[dict[str, Any]] = MongoClient(dsn, **options)
        try:
            return client[database_name]
        except Exception:
            client.close()
            raise


def mongodb_timeout_options(dsn: str) -> dict[str, int]:
    """Apply finite operation/socket bounds while preserving stricter URI settings."""
    limits = {
        "connectTimeoutMS": 5000,
        "serverSelectionTimeoutMS": 5000,
        "socketTimeoutMS": 20000,
        "timeoutMS": 25000,
    }
    names = {name.lower(): name for name in limits}
    seen = set()
    for name, value in parse_qsl(urlparse(dsn).query, keep_blank_values=True):
        canonical = names.get(name.lower())
        if canonical is None:
            continue
        if canonical in seen:
            raise ProviderError("Duplicate MongoDB timeout option.")
        seen.add(canonical)
        try:
            timeout = int(value)
        except ValueError:
            raise ProviderError("MongoDB timeouts must be positive whole milliseconds.") from None
        if not 1 <= timeout <= limits[canonical]:
            raise ProviderError("MongoDB timeout is outside the supported finite range.")
        limits[canonical] = timeout
    return limits


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
    database = unquote(parsed.path.lstrip("/").split("/", 1)[0])
    if not database:
        raise ProviderError("MongoDB DSN must include a database name.")
    return database


def mongodb_users_from_documents(
    documents: list[dict[str, Any]], *, fallback_schema: int
) -> ProviderUsers:
    """Normalize a complete MongoDB read without altering stored documents."""
    documents = [item for item in documents if item.get("username") != "__sftpwarden_schema__"]
    versions = [
        int(item["schema_version"]) for item in documents if item.get("schema_version") is not None
    ]
    normalized = [mongodb_document_to_user_mapping(item) for item in documents]
    if versions:
        version = max(versions)
    elif normalized:
        version = detect_mapping_schema({"users": normalized}, fallback_schema=1).version
    else:
        version = fallback_schema
    return user_schema(version).users_from_mapping({"users": normalized})


def mongodb_document_from_user(user: SFTPUser, *, schema_version: int = 1) -> dict[str, Any]:
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
    schema = user_schema(schema_version)
    document = schema.user_to_mapping(user)
    if schema.include_schema_version:
        document["schema_version"] = schema.version
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
    return SFTPUser.model_validate(mongodb_document_to_user_mapping(document))


def mongodb_document_to_user_mapping(document: dict[str, Any]) -> dict[str, Any]:
    """Return a provider user mapping from one MongoDB document."""
    data = dict(document)
    data.pop("_id", None)
    data.pop("schema_version", None)
    return data
