"""Schema recovery rejects ambiguous configuration and preserves uncertain effects."""

import pytest

from sftpwarden.config import load_config
from sftpwarden.providers import provider_from_config
from sftpwarden.services import schema_transition as transition
from sftpwarden.users import ProviderUsers
from sftpwarden.utils.errors import ProviderError


def test_schema_recovery_requires_local_configuration_and_valid_journal(local_project_factory):
    root, entry = local_project_factory()
    with pytest.raises(ProviderError, match="No pending"):
        transition.resume_schema_transition(entry, load_config(entry.config))
    journal = transition.transition_path(entry)
    journal.parent.mkdir()
    journal.write_text("invalid")
    with pytest.raises(ProviderError, match="recovery record"):
        transition.pending_transition(entry)
    entry.config = ""
    with pytest.raises(ProviderError, match="local project"):
        transition.transition_path(entry)


@pytest.mark.parametrize("problem", ["retarget", "downgrade", "uncertain-read"])
def test_schema_transition_preconditions_and_uncertain_write_keep_original_state(
    local_project_factory, monkeypatch, problem
):
    root, entry = local_project_factory()
    config = load_config(entry.config)
    provider = provider_from_config(root, config)
    source = provider.read()
    target = ProviderUsers(schema_version=1 if problem == "downgrade" else source.schema_version)
    if problem == "retarget":
        config.provider.path = "/etc/sftpwarden/other.yaml"
    if problem == "uncertain-read":

        def fail(*args):
            raise ProviderError("uncertain provider effect")

        monkeypatch.setattr(provider, "replace_if_unchanged", fail)
        monkeypatch.setattr(transition, "read_schema_snapshot", fail)
    with pytest.raises(ProviderError):
        transition.publish_schema_transition(entry, config, source, target, provider=provider)
    assert provider.read() == source
    assert transition.transition_path(entry).exists() == (problem == "uncertain-read")


def test_transition_refuses_provider_retargeting(local_project_factory):
    _, entry = local_project_factory()
    config = load_config(entry.config)
    users = ProviderUsers(schema_version=config.provider.user_schema)
    record = transition.SchemaTransition(
        provider=config.provider.model_copy(), source=users, target=users
    )
    config.provider.path = "/etc/sftpwarden/different.yaml"
    with pytest.raises(ProviderError, match="configuration changed"):
        transition.check_transition_config(record, config)
