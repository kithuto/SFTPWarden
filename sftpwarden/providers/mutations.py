from sftpwarden.users.models import ProviderUsers, SFTPUser
from sftpwarden.users.schemas import migrate_provider_users
from sftpwarden.users.service import find_user, remove_user, upsert_user, users_fingerprint
from sftpwarden.utils.errors import ProviderError

__all__ = ["find_user", "remove_user", "upsert_user", "users_fingerprint"]


def mutation_conflict() -> ProviderError:
    """Return a safe, actionable stale-write error without exposing credentials."""
    return ProviderError(
        "Provider mutation conflict: the user or schema changed after it was read.",
        suggestion="Reload the provider and retry the intended edit.",
    )


def prepare_user_mutation(
    current: ProviderUsers,
    expected: ProviderUsers,
    username: str,
    replacement: SFTPUser | None,
    *,
    migrate_schema: bool = False,
) -> tuple[ProviderUsers, bool]:
    """Compare one user and merge into the current snapshot, including metadata."""
    migrated = current.schema_version != expected.schema_version
    if migrated:
        if not migrate_schema or current.schema_version > expected.schema_version:
            raise mutation_conflict()
        current = migrate_provider_users(current, to_version=expected.schema_version)
    current_user = next((user for user in current.users if user.username == username), None)
    expected_user = next((user for user in expected.users if user.username == username), None)
    if current_user != expected_user:
        raise mutation_conflict()
    if replacement is None:
        return remove_user(current, username), migrated
    if replacement.username != username:
        raise ProviderError("A user mutation cannot rename the user.")
    return upsert_user(current, replacement), migrated


def require_unchanged_snapshot(current: ProviderUsers, expected: ProviderUsers) -> None:
    """Reject stale imports and schema migrations, including metadata-only edits."""
    if current != expected:
        raise mutation_conflict()
