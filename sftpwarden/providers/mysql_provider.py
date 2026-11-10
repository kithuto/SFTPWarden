from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any, ClassVar
from urllib.parse import parse_qsl, unquote, urlparse

from sftpwarden.config import ProviderType
from sftpwarden.providers.base import BaseProvider
from sftpwarden.providers.registry import register_provider
from sftpwarden.providers.sql import (
    create_sql_user_keys_table_if_missing_statement,
    create_sql_user_keys_table_statement,
    create_sql_users_table_statement,
    delete_missing_sql_users,
    delete_sql_user,
    execute_validated_sql,
    schema_uses_key_table,
    sql_check_table_query,
    sql_select_user_keys_query,
    sql_select_users_query,
    sql_user_keys_table,
    upsert_sql_user,
    upsert_sql_users,
    users_from_sql_rows,
    validate_sql_read_query,
    validate_sql_table,
)
from sftpwarden.providers.sql_mutations import mutate_sql_user, replace_sql_snapshot
from sftpwarden.security.environment import resolve_provider_dsn
from sftpwarden.security.provider_errors import private_database_errors
from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.utils.errors import ProviderError


class PyMySQLProvider(BaseProvider):
    """Shared PyMySQL-backed provider implementation."""

    provider_label: ClassVar[str] = "MySQL"
    allowed_schemes: ClassVar[set[str]] = {"mysql", "mysql+pymysql"}
    default_port: ClassVar[int] = 3306

    @classmethod
    def empty_text(cls) -> str:
        """Return an empty document for SQL providers.

        Returns
        -------
        str
            Empty string because SQL providers do not use seed files.
        """
        return ""

    @private_database_errors
    def read(self) -> ProviderUsers:
        """Read users from a PyMySQL-compatible database.

        Returns
        -------
        ProviderUsers
            Users loaded from the database.
        """
        connection = self._connect(dict_cursor=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute("set transaction isolation level repeatable read")
                cursor.execute("start transaction with consistent snapshot, read only")
                query = self.config.query or sql_select_users_query(self.config.table)
                if self.config.query:
                    validate_sql_read_query(query)
                execute_validated_sql(cursor, query)
                rows = list(cursor.fetchall())
                key_rows = None
                if schema_uses_key_table(self.config.user_schema):
                    execute_validated_sql(cursor, sql_select_user_keys_query(self.config.table))
                    key_rows = list(cursor.fetchall())
                return users_from_sql_rows(
                    rows,
                    key_rows=key_rows,
                    schema_version=self.config.user_schema,
                )
        finally:
            connection.close()

    @private_database_errors
    def write(self, users: ProviderUsers) -> None:
        """Replace the users table with a desired user set.

        Parameters
        ----------
        users
            Desired provider users.
        """
        self.ensure_schema_storage(users.schema_version)
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                upsert_sql_users(
                    cursor,
                    self.config.table,
                    users,
                    dialect="mysql",
                    schema_version=users.schema_version,
                )
                delete_missing_sql_users(cursor, self.config.table, users)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @private_database_errors
    def upsert_user(self, user: SFTPUser) -> None:
        """Create or update one database user row.

        Parameters
        ----------
        user
            User to persist.
        """
        self.ensure_schema_storage(self.config.user_schema)
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                upsert_sql_user(
                    cursor,
                    self.config.table,
                    user,
                    dialect="mysql",
                    schema_version=self.config.user_schema,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @private_database_errors
    def remove_user(self, username: str) -> None:
        """Remove one database user row.

        Parameters
        ----------
        username
            Username to remove.
        """
        self.ensure_schema_storage(self.config.user_schema)
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                delete_sql_user(
                    cursor,
                    self.config.table,
                    username,
                    schema_version=self.config.user_schema,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _checked_mutation(self, operation: Callable[[Any], None]) -> None:
        for attempt in range(3):
            try:
                self._mutation_transaction(operation)
                return
            except Exception as exc:
                if getattr(exc, "args", ())[:1] == (1213,) and attempt < 2:
                    continue
                if isinstance(exc, ProviderError):
                    raise
                raise ProviderError(
                    "SQL mutation failed or conflicted; reload the provider before retrying."
                ) from exc

    def _mutation_transaction(self, operation: Callable[[Any], None]) -> None:
        connection = self._connect(dict_cursor=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute("set transaction isolation level serializable")
                cursor.execute("set session innodb_lock_wait_timeout = 5")
                operation(cursor)
            connection.commit()
        except Exception:
            with suppress(Exception):
                connection.rollback()
            raise
        finally:
            connection.close()

    @private_database_errors
    def mutate_user(
        self,
        expected: ProviderUsers,
        username: str,
        replacement: SFTPUser | None,
        *,
        migrate_schema: bool = False,
    ) -> None:
        """Compare and edit one user while holding transactional row locks."""
        if migrate_schema:
            self.ensure_schema_storage(expected.schema_version)
        self._checked_mutation(
            lambda cursor: mutate_sql_user(
                cursor,
                self.config.table,
                expected,
                username,
                replacement,
                dialect="mysql",
                source_schema=self.config.user_schema,
                migrate_schema=migrate_schema,
            )
        )

    @private_database_errors
    def replace_if_unchanged(self, expected: ProviderUsers, replacement: ProviderUsers) -> None:
        """Check the complete source snapshot before a transactional bulk replacement."""
        if replacement.schema_version != expected.schema_version:
            self.ensure_schema_storage(replacement.schema_version)
        self._checked_mutation(
            lambda cursor: replace_sql_snapshot(
                cursor, self.config.table, expected, replacement, dialect="mysql"
            )
        )

    @private_database_errors
    def table_exists(self) -> bool:
        """Return whether the configured provider storage exists.

        Returns
        -------
        bool
            ``True`` when all tables required by the active schema can be queried.
        """
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                execute_validated_sql(cursor, sql_check_table_query(self.config.table))
                if schema_uses_key_table(self.config.user_schema):
                    execute_validated_sql(
                        cursor,
                        sql_check_table_query(sql_user_keys_table(self.config.table)),
                    )
                return True
        except Exception as exc:
            if getattr(exc, "args", [None])[0] == 1146:
                return False
            raise
        finally:
            connection.close()

    @private_database_errors
    def create_table(self) -> None:
        """Create the configured users table."""
        validate_sql_table(self.config.table)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                execute_validated_sql(cursor, create_sql_users_table_statement(self.config.table))
                if schema_uses_key_table(self.config.user_schema):
                    execute_validated_sql(
                        cursor,
                        create_sql_user_keys_table_statement(self.config.table),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @private_database_errors
    def ensure_schema_storage(self, schema_version: int) -> None:
        """Ensure tables required by a user schema exist."""
        validate_sql_table(self.config.table)
        if not schema_uses_key_table(schema_version):
            return
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                execute_validated_sql(
                    cursor,
                    create_sql_user_keys_table_if_missing_statement(self.config.table),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self, *, dict_cursor: bool = False) -> Any:
        """Open a configured PyMySQL connection."""
        if not self.config.dsn:
            raise ProviderError(f"{self.provider_label} provider requires dsn.")
        try:
            import pymysql
        except ImportError as exc:
            raise ProviderError(
                (
                    f"{self.provider_label} provider requires the mysql optional dependency; "
                    "the mariadb extra is an equivalent alias."
                ),
                suggestion='Install SFTPWarden with "sftpwarden[mysql]" or "sftpwarden[mariadb]".',
            ) from exc
        kwargs = pymysql_connect_kwargs(
            resolve_provider_dsn(self.config.dsn),
            allowed_schemes=self.allowed_schemes,
            default_port=self.default_port,
            provider_label=self.provider_label,
        )
        if dict_cursor:
            kwargs["cursorclass"] = pymysql.cursors.DictCursor
        return pymysql.connect(**kwargs)


@register_provider
class MySQLProvider(PyMySQLProvider):
    """MySQL-backed user provider."""

    provider_type = ProviderType.MYSQL
    provider_label: ClassVar[str] = "MySQL"
    allowed_schemes: ClassVar[set[str]] = {"mysql", "mysql+pymysql"}


@register_provider
class MariaDBProvider(PyMySQLProvider):
    """MariaDB-backed user provider using the PyMySQL-compatible implementation."""

    provider_type = ProviderType.MARIADB
    provider_label: ClassVar[str] = "MariaDB"
    allowed_schemes: ClassVar[set[str]] = {"mariadb", "mariadb+pymysql"}


def pymysql_connect_kwargs(
    dsn: str,
    *,
    allowed_schemes: set[str],
    default_port: int,
    provider_label: str,
) -> dict[str, Any]:
    """Parse a PyMySQL-compatible DSN into connection keyword arguments.

    Parameters
    ----------
    dsn
        Database connection URL.
    allowed_schemes
        Accepted URL schemes.
    default_port
        Default TCP port.
    provider_label
        User-facing provider label.

    Returns
    -------
    dict[str, Any]
        Connection keyword arguments for PyMySQL.
    """
    try:
        parsed = urlparse(dsn)
        port = parsed.port
        query_options = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ProviderError("Invalid MySQL/MariaDB connection URL.") from None
    if parsed.fragment:
        raise ProviderError("MySQL/MariaDB connection URLs must not contain a fragment.")
    if parsed.scheme not in allowed_schemes:
        schemes = " or ".join(f"{scheme}://" for scheme in sorted(allowed_schemes))
        raise ProviderError(f"{provider_label} DSN must use {schemes}.")
    kwargs: dict[str, Any] = {
        "host": parsed.hostname or "localhost",
        "port": port or default_port,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
        "connect_timeout": 5,
        "read_timeout": 20,
        "write_timeout": 20,
    }
    allowed = {
        "charset",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "ssl_ca",
        "ssl_cert",
        "ssl_key",
        "ssl_verify_cert",
        "ssl_verify_identity",
        "ssl_disabled",
    }
    options: dict[str, str] = {}
    for name, value in query_options:
        if name not in allowed or name in options:
            raise ProviderError("Unsupported or duplicate MySQL/MariaDB DSN option.")
        if not value:
            raise ProviderError("MySQL/MariaDB DSN options must not be empty.")
        options[name] = value
    for name, value in options.items():
        if name.endswith("_timeout"):
            try:
                timeout = int(value)
            except ValueError:
                raise ProviderError(
                    "MySQL/MariaDB timeouts must be positive whole seconds."
                ) from None
            if not 1 <= timeout <= 30:
                raise ProviderError("MySQL/MariaDB timeouts must be between 1 and 30 seconds.")
            kwargs[name] = timeout
        elif name in {"ssl_verify_cert", "ssl_verify_identity", "ssl_disabled"}:
            if value.lower() not in {"true", "false", "1", "0"}:
                raise ProviderError("MySQL/MariaDB TLS flags must be true or false.")
            kwargs[name] = value.lower() in {"true", "1"}
        else:
            kwargs[name] = value
    tls_requested = any(name.startswith("ssl_") and name != "ssl_disabled" for name in options)
    tls_requested = tls_requested or kwargs.get("ssl_disabled") is False
    if tls_requested:
        if kwargs.get("ssl_disabled") or any(
            kwargs.get(name) is False for name in ("ssl_verify_cert", "ssl_verify_identity")
        ):
            raise ProviderError(
                "Requested MySQL/MariaDB TLS requires certificate and hostname verification."
            )
        if "ssl_key" in options and "ssl_cert" not in options:
            raise ProviderError("MySQL/MariaDB ssl_key requires ssl_cert.")
        kwargs.update(ssl_verify_cert=True, ssl_verify_identity=True)
    return kwargs


def mysql_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Parse a MySQL DSN into PyMySQL keyword arguments.

    Parameters
    ----------
    dsn
        MySQL connection URL.

    Returns
    -------
    dict[str, Any]
        Connection keyword arguments for PyMySQL.
    """
    return pymysql_connect_kwargs(
        dsn,
        allowed_schemes=MySQLProvider.allowed_schemes,
        default_port=MySQLProvider.default_port,
        provider_label=MySQLProvider.provider_label,
    )


def mariadb_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Parse a MariaDB DSN into PyMySQL keyword arguments.

    Parameters
    ----------
    dsn
        MariaDB connection URL.

    Returns
    -------
    dict[str, Any]
        Connection keyword arguments for PyMySQL.
    """
    return pymysql_connect_kwargs(
        dsn,
        allowed_schemes=MariaDBProvider.allowed_schemes,
        default_port=MariaDBProvider.default_port,
        provider_label=MariaDBProvider.provider_label,
    )
