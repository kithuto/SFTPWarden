"""Validate the SSH wire structure without an external executable or crypto dependency."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

PUBLIC_KEY_PREFIXES = (
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "sk-ecdsa-sha2-nistp256@openssh.com ",
    "sk-ssh-ed25519@openssh.com ",
    "ssh-ed25519 ",
    "ssh-rsa ",
)
_INVALID = "Unsupported or invalid SSH public key."


@dataclass
class _WireReader:
    blob: bytes
    position: int = 0

    def string(self) -> bytes:
        if self.position + 4 > len(self.blob):
            raise ValueError(_INVALID)
        length = int.from_bytes(self.blob[self.position : self.position + 4], "big")
        start = self.position + 4
        end = start + length
        if end > len(self.blob):
            raise ValueError(_INVALID)
        self.position = end
        return self.blob[start:end]

    def positive_mpint(self) -> None:
        value = self.string()
        # RFC 4251: signed, minimal two's-complement encoding. RSA values are positive.
        if not value or value[0] & 0x80:
            raise ValueError(_INVALID)
        if value[0] == 0 and (len(value) == 1 or not value[1] & 0x80):
            raise ValueError(_INVALID)


def _validate_blob(kind: str, blob: bytes) -> None:
    reader = _WireReader(blob)
    if reader.string() != kind.encode("ascii"):
        raise ValueError(_INVALID)
    if kind == "ssh-rsa":
        reader.positive_mpint()
        reader.positive_mpint()
    elif "ed25519" in kind:
        if len(reader.string()) != 32:
            raise ValueError(_INVALID)
    else:
        curve = kind.removeprefix("sk-").removeprefix("ecdsa-sha2-").split("@", 1)[0]
        if reader.string() != curve.encode("ascii"):
            raise ValueError(_INVALID)
        width = {"nistp256": 32, "nistp384": 48, "nistp521": 66}[curve]
        point = reader.string()
        # Match OpenSSH's get_ec: it only accepts uncompressed SEC1 points.
        if not point or point[0] != 4 or len(point) != 1 + 2 * width:
            raise ValueError(_INVALID)
    # OpenSSH reads the security-key application as a C string; NUL is invalid.
    if kind.startswith("sk-") and b"\x00" in reader.string():
        raise ValueError(_INVALID)
    if reader.position != len(blob):
        raise ValueError(_INVALID)


def normalize_public_key(value: str) -> str:
    """Validate one key's type and wire fields, preserving its optional comment.

    This checks the public-key encoding, not possession of the private key or
    compatibility with a particular server's algorithm policy.
    """
    stripped = value.strip()
    if (
        not stripped.startswith(PUBLIC_KEY_PREFIXES)
        or len(stripped.splitlines()) != 1
        or any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in stripped)
    ):
        raise ValueError(_INVALID)
    parts = stripped.split()
    try:
        blob = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except (IndexError, UnicodeError, binascii.Error) as exc:
        raise ValueError(_INVALID) from exc
    _validate_blob(parts[0], blob)
    return stripped
