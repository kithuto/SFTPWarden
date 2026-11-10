"""A disposable alias must never grant ownership of the supplied candidate image."""

import json
from types import SimpleNamespace

import pytest

from tests.release_validation import conftest as release


def test_owned_container_cleanup_removes_its_anonymous_volumes(monkeypatch):
    commands = []

    def run(args, **_):
        commands.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release, "run_external", run)
    release.remove_docker_container("owned-database")
    assert commands == [["docker", "rm", "-f", "-v", "owned-database"]]


def test_restoring_a_local_alias_preserves_the_borrowed_candidate(monkeypatch):
    commands = []
    monkeypatch.setattr(release, "docker_image_id", lambda _: "candidate-id")
    monkeypatch.setattr(release, "run_external", lambda args, **_: commands.append(args))
    release.preserve_or_remove_image("sftpwarden:local", "original-id")
    assert commands == [["docker", "image", "tag", "original-id", "sftpwarden:local"]]


@pytest.mark.parametrize(
    "references", [{}, {"RepoTags": ["borrowed:tag"]}, {"RepoDigests": ["repo@sha256:digest"]}]
)
def test_owned_build_is_removed_only_after_its_last_reference_is_gone(monkeypatch, references):
    commands = []
    monkeypatch.setattr(release, "docker_image_id", lambda _: "created-id")

    def run(args, **_):
        commands.append(args)
        return SimpleNamespace(stdout=json.dumps([references]))

    monkeypatch.setattr(release, "run_external", run)
    release.preserve_or_remove_image("sftpwarden:local", "original-id", created_image=True)
    assert (["docker", "image", "rm", "created-id"] in commands) == (not references)


def test_restoration_failure_stops_cleanup_instead_of_losing_the_original(monkeypatch):
    monkeypatch.setattr(release, "docker_image_id", lambda _: "candidate-id")

    def run(args, **kwargs):
        assert args[2] == "tag" and kwargs.get("check", True)
        raise OSError("restoration failed")

    monkeypatch.setattr(release, "run_external", run)
    with pytest.raises(OSError, match="restoration failed"):
        release.preserve_or_remove_image("sftpwarden:local", "original-id", created_image=True)
