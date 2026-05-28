"""Tests for A2A Agent Card signing + verification — Phase 5 of OpenSpec
``20260527-a2a-agent-interop``.

Uses ``cryptography`` to generate an ephemeral RSA key, mints a JWT-SVID with
:mod:`jwt` (pyjwt — already in core deps), and exercises the verification
contract end to end against trust-domain + audience + expiry rules.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from acc.a2a.signing import (
    CardVerificationError,
    read_jwt_svid_file,
    sign_card,
    spire_x5c_scheme,
    spiffe_id_trust_domain,
    verify_signed_card,
)


# --------------------------------------------------------------------------
# Test key + JWT-SVID minting (stands in for SPIRE for the unit tests)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def issuer_keys():
    """A throwaway RSA keypair we use to sign + verify JWT-SVIDs in tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {"private": private_pem, "public": public_pem}


def _mint_jwt_svid(
    issuer_keys,
    *,
    sub: str,
    aud: str | list[str] | None = None,
    exp_offset_s: int = 60,
    iat_offset_s: int = 0,
) -> str:
    now = int(time.time())
    payload: dict = {"sub": sub, "iat": now + iat_offset_s, "exp": now + exp_offset_s}
    if aud is not None:
        payload["aud"] = aud
    return jwt.encode(payload, issuer_keys["private"], algorithm="RS256")


# --------------------------------------------------------------------------
# sign_card / read_jwt_svid_file
# --------------------------------------------------------------------------


def test_sign_card_wraps_with_svid():
    envelope = sign_card({"name": "x"}, "fake-jwt")
    assert envelope == {"card": {"name": "x"}, "svid": "fake-jwt"}


def test_sign_card_rejects_empty_svid():
    with pytest.raises(ValueError):
        sign_card({}, "")
    with pytest.raises(ValueError):
        sign_card({}, "   ")


def test_read_jwt_svid_file_trims_whitespace(tmp_path):
    p = tmp_path / "jwt.svid"
    p.write_text("  eyJhbGciOi...\n", encoding="utf-8")
    assert read_jwt_svid_file(p) == "eyJhbGciOi..."


# --------------------------------------------------------------------------
# spire_x5c_scheme — populates authentication.schemes
# --------------------------------------------------------------------------


def test_spire_x5c_scheme_carries_trust_domain():
    s = spire_x5c_scheme("acc-prod.example.com")
    assert s["scheme"] == "spire-jwt-svid"
    assert s["trustDomain"] == "acc-prod.example.com"
    assert s["openSpec"] == "20260527-a2a-agent-interop"


# --------------------------------------------------------------------------
# verify_signed_card — happy path + the security failure cases
# --------------------------------------------------------------------------


def test_verify_signed_card_success(issuer_keys):
    svid = _mint_jwt_svid(issuer_keys, sub="spiffe://acc.example/role/coding_agent")
    envelope = sign_card({"name": "coding_agent@sol-01"}, svid)
    card = verify_signed_card(
        envelope, issuer_key=issuer_keys["public"],
        expected_trust_domain="acc.example",
    )
    assert card == {"name": "coding_agent@sol-01"}


def test_verify_signed_card_rejects_wrong_trust_domain(issuer_keys):
    """The SPIFFE id in 'sub' MUST be in the expected trust domain — a peer
    in another domain doesn't get to publish cards for us."""
    svid = _mint_jwt_svid(issuer_keys, sub="spiffe://other.example/role/x")
    envelope = sign_card({}, svid)
    with pytest.raises(CardVerificationError, match="trust domain"):
        verify_signed_card(
            envelope, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )


def test_verify_signed_card_rejects_expired_token(issuer_keys):
    svid = _mint_jwt_svid(
        issuer_keys, sub="spiffe://acc.example/role/x",
        exp_offset_s=-120,  # expired 2 minutes ago
        iat_offset_s=-300,
    )
    envelope = sign_card({}, svid)
    with pytest.raises(CardVerificationError):
        verify_signed_card(
            envelope, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )


def test_verify_signed_card_rejects_bad_signature(issuer_keys):
    """A JWT signed by SOME OTHER key must fail verification — pretending to
    be a SPIRE-issued SVID requires the actual SPIRE key."""
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    forged = jwt.encode(
        {"sub": "spiffe://acc.example/role/x",
         "iat": int(time.time()), "exp": int(time.time()) + 60},
        other_pem, algorithm="RS256",
    )
    envelope = sign_card({}, forged)
    with pytest.raises(CardVerificationError):
        verify_signed_card(
            envelope, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )


def test_verify_signed_card_enforces_audience_when_set(issuer_keys):
    svid = _mint_jwt_svid(
        issuer_keys, sub="spiffe://acc.example/role/x", aud="hub.acc.example",
    )
    envelope = sign_card({}, svid)
    # Wrong audience → fails.
    with pytest.raises(CardVerificationError):
        verify_signed_card(
            envelope, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
            expected_audience="someone-else",
        )
    # Right audience → succeeds.
    card = verify_signed_card(
        envelope, issuer_key=issuer_keys["public"],
        expected_trust_domain="acc.example",
        expected_audience="hub.acc.example",
    )
    assert card == {}


def test_verify_signed_card_rejects_malformed_envelope(issuer_keys):
    """Defence-in-depth: a peer that ships a wrongly shaped envelope must be
    rejected before any JWT decoding."""
    with pytest.raises(CardVerificationError, match="envelope"):
        verify_signed_card(
            "not a dict", issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )
    with pytest.raises(CardVerificationError, match="envelope"):
        verify_signed_card(
            {"card": {"x": 1}}, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )
    with pytest.raises(CardVerificationError, match="envelope"):
        verify_signed_card(
            {"svid": "jwt..."}, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )


def test_verify_signed_card_rejects_empty_svid(issuer_keys):
    with pytest.raises(CardVerificationError, match="empty"):
        verify_signed_card(
            {"card": {}, "svid": ""}, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )


def test_verify_signed_card_requires_sub_claim(issuer_keys):
    """A JWT without a ``sub`` claim isn't a SPIFFE SVID."""
    bogus = jwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 60},
        issuer_keys["private"], algorithm="RS256",
    )
    envelope = sign_card({}, bogus)
    with pytest.raises(CardVerificationError):
        verify_signed_card(
            envelope, issuer_key=issuer_keys["public"],
            expected_trust_domain="acc.example",
        )


# --------------------------------------------------------------------------
# spiffe_id_trust_domain — small helper
# --------------------------------------------------------------------------


def test_spiffe_id_trust_domain_parses():
    assert spiffe_id_trust_domain("spiffe://acc.example/role/x") == "acc.example"
    assert spiffe_id_trust_domain("spiffe://acc.example") == "acc.example"
    assert spiffe_id_trust_domain("not-a-spiffe-id") == ""
