import base64
import json
from pathlib import Path

import pytest

from sftpwarden.users.models import normalize_public_key, public_key_fingerprint

# Public-only OpenSSH-generated fixtures; private keys were destroyed after generation.
KEYS = json.loads((Path(__file__).parents[1] / "fixtures/ssh_public_keys.json").read_text())
ED = KEYS["ed25519"]["public_key"]


def wire(*fields: bytes) -> bytes:
    return b"".join(len(field).to_bytes(4, "big") + field for field in fields)


def key_line(kind: str, blob: bytes) -> str:
    return kind + " " + base64.b64encode(blob).decode("ascii")


@pytest.mark.parametrize("fixture", KEYS.values(), ids=KEYS)
def test_real_openssh_keys_and_fingerprints(fixture: dict) -> None:
    key = fixture["public_key"]
    assert normalize_public_key("  " + key + " operator comment\n") == key + " operator comment"
    # Expected fingerprints were obtained independently with ssh-keygen -lf -E sha256.
    assert public_key_fingerprint(key) == fixture["fingerprint"]


@pytest.mark.parametrize(
    "key",
    [
        "ssh-rsa " + ED.split()[1],
        "ssh-ed25519 ZmFrZQ==",
        key_line("ssh-ed25519", wire(b"ssh-ed25519", b"x" * 31)),
        key_line("ssh-ed25519", wire(b"ssh-ed25519", b"x" * 33)),
        key_line("ssh-ed25519", wire(b"ssh-ed25519", b"x" * 32) + b"extra"),
        key_line("ssh-ed25519", b"\xff\xff\xff\xffssh-ed25519"),
        key_line("ssh-rsa", wire(b"ssh-rsa", b"", b"\x01")),
        key_line("ssh-rsa", wire(b"ssh-rsa", b"\x80", b"\x01")),
        key_line("ssh-rsa", wire(b"ssh-rsa", b"\x00\x01", b"\x01")),
        key_line("ssh-rsa", wire(b"ssh-rsa", b"\x00", b"\x01")),
        key_line(
            "ecdsa-sha2-nistp256", wire(b"ecdsa-sha2-nistp256", b"nistp384", b"\x04" + b"x" * 64)
        ),
        key_line(
            "ecdsa-sha2-nistp256", wire(b"ecdsa-sha2-nistp256", b"nistp256", b"\x04" + b"x" * 63)
        ),
        key_line(
            "ecdsa-sha2-nistp256", wire(b"ecdsa-sha2-nistp256", b"nistp256", b"\x01" + b"x" * 64)
        ),
        key_line("ecdsa-sha2-nistp256", wire(b"ecdsa-sha2-nistp256", b"nistp256", b"")),
        key_line("sk-ssh-ed25519@openssh.com", wire(b"sk-ssh-ed25519@openssh.com", b"x" * 32)),
        key_line(
            "sk-ssh-ed25519@openssh.com",
            wire(b"sk-ssh-ed25519@openssh.com", b"x" * 32, b"ssh:\x00hidden"),
        ),
        ED + "\n" + ED,
        ED + "\rhidden",
        ED + " comment\x00hidden",
    ],
)
def test_malformed_public_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="SSH public key"):
        normalize_public_key(key)


@pytest.mark.parametrize("curve,width", [("nistp256", 32), ("nistp384", 48), ("nistp521", 66)])
def test_compressed_ecdsa_points_are_rejected_like_openssh(curve: str, width: int) -> None:
    original = base64.b64decode(KEYS[curve]["public_key"].split()[1])
    point = original[-(1 + 2 * width) :]
    compressed = bytes([2 + (point[-1] & 1)]) + point[1 : 1 + width]
    kind = "ecdsa-sha2-" + curve
    key = key_line(kind, wire(kind.encode(), curve.encode(), compressed))
    with pytest.raises(ValueError, match="SSH public key"):
        normalize_public_key(key)


def test_security_key_wire_formats() -> None:
    ed_kind = "sk-ssh-ed25519@openssh.com"
    ed_blob = base64.b64decode(ED.split()[1])[-32:]
    ed_key = key_line(ed_kind, wire(ed_kind.encode(), ed_blob, b"ssh:operator"))
    ec_kind = "sk-ecdsa-sha2-nistp256@openssh.com"
    ec_blob = base64.b64decode(KEYS["nistp256"]["public_key"].split()[1])[-65:]
    ec_key = key_line(ec_kind, wire(ec_kind.encode(), b"nistp256", ec_blob, b"ssh:"))
    for key in (ed_key, ec_key):
        assert normalize_public_key(key) == key
