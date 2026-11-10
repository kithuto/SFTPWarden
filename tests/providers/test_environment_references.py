import pytest

from sftpwarden.security.environment import dsn_environment_names, resolve_provider_dsn
from sftpwarden.utils.errors import ProviderError


@pytest.mark.parametrize(
    "value", ["${MISSING}", "$MISSING", "%MISSING%", "mysql://u:${MISSING}@db/test"]
)
@pytest.mark.parametrize("environment", [{}, {"MISSING": ""}])
def test_missing_or_empty_reference_fails_before_connecting(value, environment) -> None:
    with pytest.raises(ProviderError, match="MISSING.*missing or empty"):
        resolve_provider_dsn(value, environment=environment)


def test_explicit_embedded_references_expand_only_once() -> None:
    assert (
        resolve_provider_dsn(
            "mysql://${USER}:${PASSWORD}@db/test",
            environment={"USER": "alice", "PASSWORD": "literal${OTHER}$;&%", "OTHER": "wrong"},
        )
        == "mysql://alice:literal${OTHER}$;&%@db/test"
    )


@pytest.mark.parametrize(
    "value", ["${", "${BAD-NAME}", "mysql://u:${BAD-NAME}@db/test", "${DB:-fallback}"]
)
def test_malformed_reference_fails_without_echoing_dsn(value) -> None:
    with pytest.raises(ProviderError, match="Invalid provider DSN environment reference"):
        resolve_provider_dsn(value, environment={})


def test_discovery_deduplicates_references_and_does_not_expand_literal_password() -> None:
    assert dsn_environment_names("postgresql://${USER}:${PASS}@${USER}/test") == ("USER", "PASS")
    assert dsn_environment_names("postgresql://user:literal$PASS%PASS%@db/test") == ()
