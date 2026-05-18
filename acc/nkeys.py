"""NATS NKey generation + nats.conf authorization rendering.

Proposal 013 (Phase 0c).  This module is the standalone-mode
counterpart to the operator's Go key generation (PR-4): it mints
Ed25519 NKey identities and renders the ``authorization { users }``
block of ``nats.conf`` from the canonical permission matrix
(``acc/nats_permissions.yaml`` via :mod:`acc.nats_permissions`).

NKey wire format (see github.com/nats-io/nkeys):

* a *public* key is ``base32( prefix_byte || ed25519_pub(32) || crc16(2 LE) )``
* a *seed* is ``base32( b1 || b2 || ed25519_seed(32) || crc16(2 LE) )``
  where ``b1 = PREFIX_SEED | (PREFIX_USER >> 5)`` and
  ``b2 = (PREFIX_USER & 31) << 3``

base32 is RFC 4648 uppercase, no padding.  CRC-16 is the XMODEM
variant (poly 0x1021, init 0x0000).

Implemented in pure Python on top of ``cryptography`` (already an ACC
dependency) so the standalone CLI needs no extra package.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# The eight NKey identities (six agent roles + the operator surface +
# the edge leaf-node link).  Order is stable so generated key sets and
# rendered configs are reproducible.
NKEY_IDENTITIES: tuple[str, ...] = (
    "arbiter",
    "ingester",
    "analyst",
    "synthesizer",
    "coding_agent",
    "observer",
    "tui",
    "leaf",
)

_PREFIX_USER = 20 << 3  # 'U' role prefix
_PREFIX_SEED = 18 << 3  # 'S' seed prefix


def _crc16(data: bytes) -> int:
    """CRC-16/XMODEM — the checksum NKey strings carry."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def _b32(data: bytes) -> str:
    """RFC 4648 base32, uppercase, padding stripped — NKey encoding."""
    return base64.b32encode(data).decode("ascii").rstrip("=")


def _encode(prefix: bytes, payload: bytes) -> str:
    body = prefix + payload
    return _b32(body + _crc16(body).to_bytes(2, "little"))


def generate_user_nkey() -> tuple[str, str]:
    """Mint a fresh NATS *user* NKey.

    Returns:
        ``(seed, public_key)`` — ``seed`` is the secret half
        (``S...``-prefixed), ``public_key`` the verifier half
        (``U...``-prefixed).  The seed must be kept ``0600`` and never
        logged; the public key goes into ``nats.conf``.
    """
    priv = Ed25519PrivateKey.generate()
    raw_seed = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    raw_pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public = _encode(bytes([_PREFIX_USER]), raw_pub)
    b1 = _PREFIX_SEED | (_PREFIX_USER >> 5)
    b2 = (_PREFIX_USER & 31) << 3
    seed = _encode(bytes([b1, b2]), raw_seed)
    return seed, public


def generate_identity_keys() -> dict[str, dict[str, str]]:
    """Mint one user NKey for every entry in :data:`NKEY_IDENTITIES`.

    Returns a ``{identity: {"seed": ..., "public": ...}}`` map.
    """
    keys: dict[str, dict[str, str]] = {}
    for identity in NKEY_IDENTITIES:
        seed, public = generate_user_nkey()
        keys[identity] = {"seed": seed, "public": public}
    return keys


def render_authorization_block(public_keys: dict[str, str]) -> str:
    """Render the ``authorization { users = [...] }`` block of nats.conf.

    Args:
        public_keys: ``{identity: public_nkey}`` — the verifier halves.
            Identities absent from the permission matrix are skipped
            with no entry (so a partial key set still renders).

    The permission globs come from the canonical
    ``acc/nats_permissions.yaml`` so this renderer and the operator's
    Go renderer stay in lock step.
    """
    from acc.nats_permissions import load_permission_matrix  # noqa: PLC0415

    matrix = load_permission_matrix()
    lines: list[str] = ["authorization {", "  users = ["]
    for identity in NKEY_IDENTITIES:
        pub = public_keys.get(identity)
        perms = matrix.get(identity)
        if not pub or perms is None:
            continue
        pub_globs = ", ".join(f'"{g}"' for g in perms["publish"])
        sub_globs = ", ".join(f'"{g}"' for g in perms["subscribe"])
        lines.append(f"    {{ # {identity}")
        lines.append(f"      nkey: {pub}")
        lines.append("      permissions: {")
        lines.append(f"        publish: {{ allow: [{pub_globs}] }}")
        lines.append(f"        subscribe: {{ allow: [{sub_globs}] }}")
        lines.append("      }")
        lines.append("    }")
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"
