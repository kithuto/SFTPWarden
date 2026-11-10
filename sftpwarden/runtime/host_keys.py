"""Validate server identity before publishing keys in the Linux runtime."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from sftpwarden.config import load_config
from sftpwarden.runtime.configuration import APPLIED_CONFIG_PATH, load_runtime_config
from sftpwarden.runtime.filesystem import open_private_directory
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes, sync_directory

KEY_TYPES = {
    "ssh_host_ed25519_key": "ssh-ed25519",
    "ssh_host_rsa_key": "ssh-rsa",
    "ssh_host_ecdsa_key": "ecdsa-sha2-",
}
REQUIRE_EXISTING = ".require-existing-identity"
IDENTITY_RECORD = ".identity.json"


def restrict_key_directory(directory: Path) -> str | None:
    """Remove inherited Windows access before writing temporary private key material."""
    if os.name != "nt":
        return None
    try:
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        sid = re.search(rb"S-1-\d+(?:-\d+)+", identity.stdout)
        if sid is None:
            raise ValueError("Missing current user SID")
        subprocess.run(
            [
                "icacls",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                "*" + sid.group().decode("ascii") + ":(OI)(CI)F",
                "/remove:g",
                "*S-1-3-4",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError("Cannot protect temporary host-key material.") from None
    return sid.group().decode("ascii")


def set_key_owner(path: Path, sid: str | None) -> None:
    """Inherit only the protected directory ACL and assign the current Windows owner."""
    if sid is None:
        return
    try:
        # Creation can add explicit ACEs from the process token. Resetting to the
        # already protected parent removes those before OpenSSH inspects the file.
        subprocess.run(
            ["icacls", str(path), "/reset"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        subprocess.run(
            ["icacls", str(path), "/setowner", "*" + sid],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Cannot protect temporary host-key ownership.") from None


def validated_key(name: str, private: bytes, public: bytes | None = None) -> tuple[bytes, str]:
    """Derive public identity without printing private material or invoking a password prompt."""
    if name not in KEY_TYPES or not private or len(private) > 64 * 1024:
        raise RuntimeError("Invalid host-key name or size.")
    with tempfile.TemporaryDirectory(prefix="sftpwarden-host-key-") as temporary:
        path = Path(temporary) / "key"
        sid = restrict_key_directory(path.parent)
        atomic_bytes(path, private)
        set_key_owner(path, sid)
        try:
            result = subprocess.run(
                ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Host-key validation failed or exceeded its deadline.") from None
        fields = result.stdout.split()
        if result.returncode or len(fields) < 2:
            if (
                b"permissions" in result.stderr.lower()
                or b"permission denied" in result.stderr.lower()
            ):
                raise RuntimeError("Host-key permissions prevent OpenSSH validation.")
            if b"invalid format" in result.stderr.lower():
                raise RuntimeError("OpenSSH rejected the host-key format.")
            if b"usage:" in result.stderr.lower():
                raise RuntimeError("OpenSSH rejected the host-key validation arguments.")
            raise RuntimeError("Host-key material is invalid or encrypted.")
        if not fields[0].decode("ascii").startswith(KEY_TYPES[name]):
            raise RuntimeError("Host-key type does not match its configured filename.")
        derived = b" ".join(fields[:2])
        if public is not None and b" ".join(public.split()[:2]) != derived:
            raise RuntimeError("Host private and public keys do not match.")
        digest = hashlib.sha256(base64.b64decode(fields[1], validate=True)).digest()
        return derived + b"\n", "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def read_keys(directory: Path, *, projected_secret: bool = False) -> dict[str, tuple[bytes, bytes]]:
    """Read only known regular key files, allowing Kubernetes' internal projection links."""
    result = {}
    if projected_secret:
        allowed = {name + suffix for name in KEY_TYPES for suffix in ("", ".pub")}
        if any(
            item.name not in allowed and not item.name.startswith("..")
            for item in directory.iterdir()
        ):
            raise RuntimeError("Host-key Secret contains unsupported key entries.")
    for name in KEY_TYPES:
        path = directory / name
        if not path.exists() and not path.is_symlink():
            if path.with_name(name + ".pub").exists():
                raise RuntimeError("A public host key has no matching private key.")
            continue
        if projected_secret:
            if not path.resolve().is_relative_to(directory.resolve()):
                raise RuntimeError("Host-key Secret contains an unsafe projection path.")
        elif path.is_symlink():
            raise RuntimeError("Host identity cannot be loaded through symbolic links.")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024:
            raise RuntimeError("Host-key input must be a bounded regular file.")
        private = path.read_bytes()
        public_path = path.with_name(name + ".pub")
        if public_path.is_symlink() and (
            not projected_secret or not public_path.resolve().is_relative_to(directory.resolve())
        ):
            raise RuntimeError("Host public key has an unsafe path.")
        if public_path.exists():
            public_info = public_path.stat()
            if (
                not stat.S_ISREG(public_info.st_mode)
                or public_info.st_nlink != 1
                or public_info.st_size > 64 * 1024
            ):
                raise RuntimeError("Host public key must be a bounded regular file.")
        public, _fingerprint = validated_key(
            name,
            private,
            public_path.read_bytes() if public_path.exists() else None,
        )
        result[name] = (private, public)
    return result


def prepare_host_keys(directory: Path, *, secret: Path | None = None) -> dict[str, str]:
    """Normalize ownership/modes and never generate replacements for a configured Secret."""
    if directory.is_symlink():
        raise RuntimeError("Host-key directory cannot be a symbolic link.")
    keys = read_keys(secret, projected_secret=True) if secret is not None else read_keys(directory)
    record = directory / IDENTITY_RECORD
    if record.is_symlink():
        raise RuntimeError("Host identity record cannot be a symbolic link.")
    if record.exists():
        expected = json.loads(record.read_bytes())
        actual = {
            name: validated_key(name, private, public)[1]
            for name, (private, public) in keys.items()
        }
        if expected != actual:
            raise RuntimeError(
                "Saved host identity is incomplete or changed; restore its original material."
            )
    marker = directory / REQUIRE_EXISTING
    if (secret is not None or marker.exists()) and not keys:
        raise RuntimeError("The configured host identity is missing; refusing to generate another.")
    if secret is None and not marker.exists() and not record.exists():
        with tempfile.TemporaryDirectory(prefix="sftpwarden-generate-key-") as temporary:
            for name, algorithm in (
                ("ssh_host_ed25519_key", "ed25519"),
                ("ssh_host_rsa_key", "rsa"),
            ):
                if name in keys:
                    continue
                path = Path(temporary) / name
                command = ["ssh-keygen", "-q", "-t", algorithm, "-N", "", "-f", str(path)]
                if algorithm == "rsa":
                    command.extend(["-b", "4096"])
                try:
                    subprocess.run(command, check=True, capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    raise RuntimeError("Host-key generation failed.") from None
                keys[name] = (path.read_bytes(), path.with_name(name + ".pub").read_bytes())
    descriptor = open_private_directory(directory)
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o700)
        # Keep all privileged writes attached to the opened Linux directory even
        # if a pathname above it is renamed while preparation is in progress.
        directory = Path(f"/proc/self/fd/{descriptor}")
        marker = directory / REQUIRE_EXISTING
        fingerprints = {}
        for name, (private, public) in keys.items():
            public, fingerprint = validated_key(name, private, public)
            for suffix, content, mode in (("", private, 0o600), (".pub", public, 0o644)):
                destination = directory / (name + suffix)
                atomic_bytes(destination, content)
                file_descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fchown(file_descriptor, 0, 0)
                    os.fchmod(file_descriptor, mode)
                    os.fsync(file_descriptor)
                finally:
                    os.close(file_descriptor)
            fingerprints[name] = fingerprint
        if secret is not None:
            # Only known keys from this configured authority remain active.
            for name in KEY_TYPES.keys() - keys.keys():
                (directory / name).unlink(missing_ok=True)
                (directory / (name + ".pub")).unlink(missing_ok=True)
            atomic_bytes(
                marker, b"Configured host-key authority; do not generate replacement keys.\n"
            )
        atomic_bytes(directory / IDENTITY_RECORD, json.dumps(fingerprints, sort_keys=True).encode())
        sync_directory(directory)
        return fingerprints
    finally:
        os.close(descriptor)


def main() -> None:
    """Prepare the configured Linux runtime identity without printing private failures."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--secret", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    try:
        directory = args.directory
        if directory is None:
            path = args.config or os.environ["SFTPWARDEN_CONFIG"]
            config = (
                load_runtime_config(path) if APPLIED_CONFIG_PATH.exists() else load_config(path)
            )
            directory = Path(config.server.host_keys_dir)
            if args.state is None:
                args.state = Path(config.server.state_dir)
        if (
            args.state
            and (args.state / "state.json").exists()
            and args.secret is None
            and not read_keys(directory)
        ):
            raise RuntimeError("Existing runtime state requires a prepared host identity.")
        prepare_host_keys(directory, secret=args.secret)
    except Exception:
        raise SystemExit(
            "Host identity preparation failed; inspect its source before retrying."
        ) from None


if __name__ == "__main__":
    main()
