"""Host-independent validation of identity inputs before privileged Linux publication."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftpwarden.config import default_project_config
from sftpwarden.runtime import host_keys
from sftpwarden.runtime.host_keys import prepare_host_keys, read_keys, validated_key
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("failure", [None, "identity", "sid", "permissions"])
def test_windows_key_directory_is_private_before_material_is_written(
    tmp_path, monkeypatch, failure
):
    calls = []

    def run(command, **options):
        calls.append(command)
        assert options == {"capture_output": True, "check": True, "timeout": 10}
        assert list(tmp_path.iterdir()) == []
        if (failure == "identity" and command[0] == "whoami") or (
            failure == "permissions" and command[0] == "icacls"
        ):
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout=b"missing" if failure == "sid" else b'"user","S-1-5-21-123"')

    monkeypatch.setattr(host_keys, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(host_keys.subprocess, "run", run)
    if failure:
        with pytest.raises(RuntimeError, match="Cannot protect"):
            host_keys.restrict_key_directory(tmp_path)
    else:
        assert host_keys.restrict_key_directory(tmp_path) == "S-1-5-21-123"
    assert calls[0] == ["whoami", "/user", "/fo", "csv", "/nh"]
    if failure not in {"identity", "sid"}:
        assert calls[1] == [
            "icacls",
            str(tmp_path),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-21-123:(OI)(CI)F",
            "/remove:g",
            "*S-1-3-4",
        ]


@pytest.mark.parametrize("failure", [None, "/reset", "/setowner"])
def test_windows_private_key_owner_matches_the_account_not_its_admin_group(
    tmp_path, monkeypatch, failure
):
    calls = []

    def run(command, **options):
        calls.append(command)
        assert options == {"capture_output": True, "check": True, "timeout": 10}
        if command[2] == failure:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(host_keys.subprocess, "run", run)
    path = tmp_path / "key"
    if failure:
        with pytest.raises(RuntimeError, match="ownership"):
            host_keys.set_key_owner(path, "S-1-5-21-123")
    else:
        host_keys.set_key_owner(path, "S-1-5-21-123")
    expected = [["icacls", str(path), "/reset"]]
    if failure != "/reset":
        expected.append(["icacls", str(path), "/setowner", "*S-1-5-21-123"])
    assert calls == expected


@pytest.fixture(autouse=True)
def remove_ephemeral_identity_material(tmp_path):
    try:
        yield
    finally:
        shutil.rmtree(tmp_path)


@pytest.mark.parametrize(
    "error, message",
    [(b"Bad permissions", "permissions"), (b"invalid format", "format"), (b"usage:", "arguments")],
)
def test_host_key_validation_reports_only_safe_failure_categories(monkeypatch, error, message):
    monkeypatch.setattr(host_keys, "restrict_key_directory", lambda _: None)
    monkeypatch.setattr(
        host_keys.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout=b"", stderr=error + b" private-sentinel"
        ),
    )
    with pytest.raises(RuntimeError, match=message) as caught:
        validated_key("ssh_host_ed25519_key", b"synthetic-test-material")
    assert "private-sentinel" not in str(caught.value)


@pytest.fixture
def private_key() -> bytes:
    with tempfile.TemporaryDirectory(prefix="sftpwarden-test-key-") as directory:
        path = Path(directory) / "ephemeral-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return path.read_bytes()


def test_host_key_type_and_public_identity_must_match(private_key, monkeypatch):
    native_run = subprocess.run

    def inspect_native_validation(command, **options):
        result = native_run(command, **options)
        if command[0] == "ssh-keygen":
            # Only this generated, valid fixture reaches this adapter. On native
            # platform failures retain the tool's explanation, never key bytes.
            diagnostic = (
                result.stderr.decode("utf-8", errors="replace")
                .replace(command[-1], "<temporary-key>")
                .replace(command[-1].replace("\\", "/"), "<temporary-key>")
            )
            assert result.returncode == 0, f"{shutil.which('ssh-keygen')}: {diagnostic}"
        return result

    monkeypatch.setattr(host_keys.subprocess, "run", inspect_native_validation)
    public, fingerprint = validated_key("ssh_host_ed25519_key", private_key)
    assert public.startswith(b"ssh-ed25519 ")
    assert fingerprint.startswith("SHA256:")
    assert (
        validated_key("ssh_host_ed25519_key", private_key, public + b"operator-comment")[1]
        == fingerprint
    )
    with pytest.raises(RuntimeError, match="type"):
        validated_key("ssh_host_rsa_key", private_key)
    with pytest.raises(RuntimeError, match="do not match"):
        validated_key("ssh_host_ed25519_key", private_key, b"ssh-ed25519 AAAA")


def test_invalid_secret_never_generates_replacement(tmp_path, monkeypatch):
    source = tmp_path / "secret"
    source.mkdir()
    destination = tmp_path / "host-keys"
    monkeypatch.setattr(
        "sftpwarden.runtime.host_keys.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("generation attempted"),
    )
    with pytest.raises(RuntimeError, match="missing"):
        prepare_host_keys(destination, secret=source)
    assert not destination.exists()


def test_identity_reader_rejects_private_symlink(tmp_path, private_key):
    original = tmp_path / "original"
    original.write_bytes(private_key)
    link = tmp_path / "ssh_host_ed25519_key"
    link.symlink_to(original)
    with pytest.raises(RuntimeError, match="symbolic"):
        read_keys(tmp_path)


def test_projected_secret_cannot_escape_its_volume(tmp_path, private_key):
    original = tmp_path / "original"
    original.write_bytes(private_key)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "ssh_host_ed25519_key").symlink_to(original)
    with pytest.raises(RuntimeError, match="unsafe"):
        read_keys(secret, projected_secret=True)


def test_kubernetes_projection_reads_and_validates_material(tmp_path, private_key):
    content = tmp_path / "..generation"
    content.mkdir()
    (content / "ssh_host_ed25519_key").write_bytes(private_key)
    (tmp_path / "..data").symlink_to(content, target_is_directory=True)
    (tmp_path / "ssh_host_ed25519_key").symlink_to(Path("..data/ssh_host_ed25519_key"))
    assert read_keys(tmp_path, projected_secret=True)["ssh_host_ed25519_key"][0] == private_key


def test_missing_recorded_algorithm_is_not_regenerated(tmp_path, private_key):
    (tmp_path / "ssh_host_ed25519_key").write_bytes(private_key)
    (tmp_path / ".identity.json").write_text('{"ssh_host_rsa_key": "SHA256:lost"}')
    with pytest.raises(RuntimeError, match="incomplete or changed"):
        prepare_host_keys(tmp_path)


def test_invalid_private_material_fails_without_disclosing_it():
    with pytest.raises(RuntimeError) as caught:
        validated_key("ssh_host_ed25519_key", b"private-material-sentinel")
    assert "private-material-sentinel" not in str(caught.value)


@pytest.mark.parametrize("secret", [False, True])
def test_existing_identity_is_published_and_recorded_without_regeneration(
    linux_publication, tmp_path, private_key, monkeypatch, secret
):
    case = linux_publication
    case.attach(host_keys)
    source = tmp_path / "secret" if secret else case.directory
    source.mkdir(exist_ok=True)
    name = "ssh_host_ed25519_key"
    (source / name).write_bytes(private_key)
    (case.directory / host_keys.REQUIRE_EXISTING).write_text("preserve")
    if secret:
        (case.directory / "ssh_host_rsa_key").write_bytes(b"obsolete")
        (case.directory / "ssh_host_rsa_key.pub").write_bytes(b"obsolete")
    fingerprints = host_keys.prepare_host_keys(case.directory, secret=source if secret else None)
    assert fingerprints == json.loads((case.directory / host_keys.IDENTITY_RECORD).read_bytes())
    assert (case.directory / name).read_bytes() == private_key
    assert (case.directory / (name + ".pub")).read_bytes().startswith(b"ssh-ed25519 ")
    assert not (case.directory / "ssh_host_rsa_key").exists()
    assert case.closed == [case.descriptor]
    assert all(uid == gid == 0 for fd, uid, gid in case.changes)


def test_first_boot_generates_and_retains_both_algorithms(linux_publication):
    case = linux_publication
    case.attach(host_keys)
    first = host_keys.prepare_host_keys(case.directory)
    assert set(first) == {"ssh_host_ed25519_key", "ssh_host_rsa_key"}
    assert host_keys.prepare_host_keys(case.directory) == first
    assert case.closed == [case.descriptor, case.descriptor]


def test_key_generation_failure_never_publishes_partial_identity(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh-keygen", 60)

    monkeypatch.setattr(host_keys.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="generation failed"):
        host_keys.prepare_host_keys(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["oversize", "unknown-name", "timeout"])
def test_key_validation_rejects_unsupported_material_and_bounds_subprocess(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh-keygen", 10)

    monkeypatch.setattr(host_keys.subprocess, "run", fail)
    name = "unknown" if failure == "unknown-name" else "ssh_host_rsa_key"
    with pytest.raises(RuntimeError):
        host_keys.validated_key(name, b"x" * (65537 if failure == "oversize" else 1))


@pytest.mark.parametrize(
    "problem",
    [
        "unknown-secret",
        "orphan-public",
        "private-directory",
        "large-public",
        "public-link",
        "record-link",
        "directory-link",
    ],
)
def test_identity_storage_rejects_ambiguous_or_unsafe_paths(tmp_path, private_key, problem):
    root = tmp_path / "identity"
    root.mkdir()
    private = root / "ssh_host_ed25519_key"
    private.write_bytes(private_key)
    public = private.with_name(private.name + ".pub")
    if problem == "unknown-secret":
        (root / "unknown").write_text("unexpected")
    elif problem == "orphan-public":
        private.unlink()
        public.write_text("orphan")
    elif problem == "private-directory":
        private.unlink()
        private.mkdir()
    elif problem == "large-public":
        public.write_bytes(b"x" * 65537)
    elif problem == "public-link":
        public.symlink_to(private)
    elif problem == "record-link":
        (root / host_keys.IDENTITY_RECORD).symlink_to(private)
    else:
        linked = tmp_path / "linked"
        linked.symlink_to(root, target_is_directory=True)
        root = linked
    with pytest.raises(RuntimeError):
        if problem in {"record-link", "directory-link"}:
            host_keys.prepare_host_keys(root)
        else:
            host_keys.read_keys(root, projected_secret=problem == "unknown-secret")


@pytest.mark.parametrize("applied", [False, True])
def test_identity_entrypoint_uses_selected_config_and_rejects_missing_retained_keys(
    tmp_path, monkeypatch, applied
):
    config = default_project_config("test")
    config.server.host_keys_dir = str(tmp_path / "keys")
    config.server.state_dir = str(tmp_path / "state")
    Path(config.server.state_dir).mkdir()
    applied_path = tmp_path / "applied"
    if applied:
        applied_path.touch()
    monkeypatch.setattr(host_keys, "APPLIED_CONFIG_PATH", applied_path)
    monkeypatch.setenv("SFTPWARDEN_CONFIG", "selected.yaml")
    monkeypatch.setattr(
        host_keys, "load_runtime_config" if applied else "load_config", lambda path: config
    )
    monkeypatch.setattr(host_keys, "read_keys", lambda path: {})
    calls = []
    monkeypatch.setattr(host_keys, "prepare_host_keys", lambda path, **kwargs: calls.append(path))
    monkeypatch.setattr(host_keys.argparse._sys, "argv", ["host-keys"])
    host_keys.main()
    assert calls == [Path(config.server.host_keys_dir)]
    (Path(config.server.state_dir) / "state.json").write_text("{}")
    with pytest.raises(SystemExit, match="preparation failed"):
        host_keys.main()
    assert len(calls) == 1
