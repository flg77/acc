"""Unit tests for acc.spiffe_verify (proposal 011 PR-4).

Mints synthetic EC-signed JWT-SVIDs with a locally-generated keypair,
publishes the public half as a JWKS bundle, and exercises the verifier
across the happy path + every failure mode.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from acc.spiffe_verify import (
    SpiffeVerificationError,
    SpiffeVerifier,
    load_jwt_bundle,
    load_jwt_svid,
    verify_jwt_svid,
)


# ---------------------------------------------------------------------------
# Test-key helpers
# ---------------------------------------------------------------------------


def _keypair(kid: str):
    """Return (private_key, public_jwk_dict) for an ES256 keypair."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(priv.public_key()))
    pub_jwk["kid"] = kid
    return priv, pub_jwk


def _bundle(*public_jwks: dict) -> dict:
    return {"keys": list(public_jwks)}


def _mint(priv, kid: str, *, sub: str, aud: str,
          exp_offset: float = 300.0, extra: dict | None = None) -> str:
    """Mint a compact JWT-SVID signed by *priv*."""
    now = time.time()
    claims = {
        "sub": sub,
        "aud": aud,
        "iat": int(now),
        "exp": int(now + exp_offset),
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, priv, algorithm="ES256", headers={"kid": kid})


_ARBITER = "spiffe://acc-prod.example.com/role/research"
_AUDIENCE = "acc-role-update"


# ---------------------------------------------------------------------------
# load_jwt_svid / load_jwt_bundle
# ---------------------------------------------------------------------------


class TestFileLoading:
    def test_load_jwt_svid_ok(self, tmp_path: Path):
        (tmp_path / "jwt_svid.token").write_text("the.jwt.token\n", encoding="utf-8")
        assert load_jwt_svid(tmp_path) == "the.jwt.token"

    def test_load_jwt_svid_missing(self, tmp_path: Path):
        with pytest.raises(SpiffeVerificationError, match="cannot read"):
            load_jwt_svid(tmp_path)

    def test_load_jwt_svid_empty(self, tmp_path: Path):
        (tmp_path / "jwt_svid.token").write_text("   \n", encoding="utf-8")
        with pytest.raises(SpiffeVerificationError, match="empty"):
            load_jwt_svid(tmp_path)

    def test_load_jwt_bundle_ok(self, tmp_path: Path):
        _, jwk = _keypair("k1")
        (tmp_path / "jwt_bundle.json").write_text(
            json.dumps(_bundle(jwk)), encoding="utf-8")
        bundle = load_jwt_bundle(tmp_path)
        assert "keys" in bundle and len(bundle["keys"]) == 1

    def test_load_jwt_bundle_missing(self, tmp_path: Path):
        with pytest.raises(SpiffeVerificationError, match="cannot read"):
            load_jwt_bundle(tmp_path)

    def test_load_jwt_bundle_bad_json(self, tmp_path: Path):
        (tmp_path / "jwt_bundle.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(SpiffeVerificationError, match="not valid JSON"):
            load_jwt_bundle(tmp_path)

    def test_load_jwt_bundle_not_jwks(self, tmp_path: Path):
        (tmp_path / "jwt_bundle.json").write_text('{"foo": 1}', encoding="utf-8")
        with pytest.raises(SpiffeVerificationError, match="not a JWKS"):
            load_jwt_bundle(tmp_path)


# ---------------------------------------------------------------------------
# verify_jwt_svid — happy path
# ---------------------------------------------------------------------------


class TestVerifyHappyPath:
    def test_valid_token_verifies(self):
        priv, jwk = _keypair("k1")
        token = _mint(priv, "k1", sub=_ARBITER, aud=_AUDIENCE)
        claims = verify_jwt_svid(token, _bundle(jwk), _AUDIENCE)
        assert claims["sub"] == _ARBITER
        assert claims["aud"] == _AUDIENCE

    def test_sub_enforced_when_expected_id_given(self):
        priv, jwk = _keypair("k1")
        token = _mint(priv, "k1", sub=_ARBITER, aud=_AUDIENCE)
        claims = verify_jwt_svid(
            token, _bundle(jwk), _AUDIENCE, expected_spiffe_id=_ARBITER)
        assert claims["sub"] == _ARBITER

    def test_multi_key_bundle_picks_matching_kid(self):
        priv1, jwk1 = _keypair("k1")
        _, jwk2 = _keypair("k2")
        token = _mint(priv1, "k1", sub=_ARBITER, aud=_AUDIENCE)
        # Bundle has both keys; verifier must select k1 by the JWT kid.
        claims = verify_jwt_svid(token, _bundle(jwk2, jwk1), _AUDIENCE)
        assert claims["sub"] == _ARBITER


# ---------------------------------------------------------------------------
# verify_jwt_svid — failure modes
# ---------------------------------------------------------------------------


class TestVerifyFailures:
    def test_wrong_audience_rejected(self):
        priv, jwk = _keypair("k1")
        token = _mint(priv, "k1", sub=_ARBITER, aud="some-other-audience")
        with pytest.raises(SpiffeVerificationError, match="verification failed"):
            verify_jwt_svid(token, _bundle(jwk), _AUDIENCE)

    def test_wrong_subject_rejected(self):
        priv, jwk = _keypair("k1")
        token = _mint(priv, "k1",
                      sub="spiffe://acc-prod.example.com/role/imposter",
                      aud=_AUDIENCE)
        with pytest.raises(SpiffeVerificationError, match="not the expected arbiter"):
            verify_jwt_svid(token, _bundle(jwk), _AUDIENCE,
                            expected_spiffe_id=_ARBITER)

    def test_expired_token_rejected(self):
        priv, jwk = _keypair("k1")
        # exp 1 hour in the past (well beyond the 60s skew leeway).
        token = _mint(priv, "k1", sub=_ARBITER, aud=_AUDIENCE, exp_offset=-3600)
        with pytest.raises(SpiffeVerificationError):
            verify_jwt_svid(token, _bundle(jwk), _AUDIENCE)

    def test_tampered_signature_rejected(self):
        priv, jwk = _keypair("k1")
        # Sign with a DIFFERENT key than the one published in the bundle.
        other_priv, _ = _keypair("k1")
        token = _mint(other_priv, "k1", sub=_ARBITER, aud=_AUDIENCE)
        with pytest.raises(SpiffeVerificationError, match="verification failed"):
            verify_jwt_svid(token, _bundle(jwk), _AUDIENCE)

    def test_kid_not_in_bundle_rejected(self):
        priv, _ = _keypair("k1")
        _, other_jwk = _keypair("k2")
        token = _mint(priv, "k1", sub=_ARBITER, aud=_AUDIENCE)
        # Bundle only has k2; the JWT's kid is k1.
        with pytest.raises(SpiffeVerificationError, match="no key in the trust bundle"):
            verify_jwt_svid(token, _bundle(other_jwk), _AUDIENCE)

    def test_alg_none_rejected(self):
        # An unsigned ("alg":"none") token must never be accepted.
        unsigned = jwt.encode(
            {"sub": _ARBITER, "aud": _AUDIENCE, "exp": int(time.time() + 300)},
            key="", algorithm="none", headers={"kid": "k1"},
        )
        _, jwk = _keypair("k1")
        with pytest.raises(SpiffeVerificationError, match="unacceptable alg"):
            verify_jwt_svid(unsigned, _bundle(jwk), _AUDIENCE)

    def test_garbage_token_rejected(self):
        _, jwk = _keypair("k1")
        with pytest.raises(SpiffeVerificationError):
            verify_jwt_svid("not-a-jwt", _bundle(jwk), _AUDIENCE)


# ---------------------------------------------------------------------------
# SpiffeVerifier — file-backed end to end
# ---------------------------------------------------------------------------


class TestSpiffeVerifier:
    def test_verify_reads_bundle_from_disk(self, tmp_path: Path):
        priv, jwk = _keypair("k1")
        (tmp_path / "jwt_bundle.json").write_text(
            json.dumps(_bundle(jwk)), encoding="utf-8")
        token = _mint(priv, "k1", sub=_ARBITER, aud=_AUDIENCE)

        verifier = SpiffeVerifier(tmp_path, _AUDIENCE)
        claims = verifier.verify(token, expected_spiffe_id=_ARBITER)
        assert claims["sub"] == _ARBITER

    def test_verify_picks_up_rotated_bundle(self, tmp_path: Path):
        """The verifier re-reads the bundle each call, so a rotated
        trust bundle is honoured without an agent restart."""
        priv_old, jwk_old = _keypair("old")
        priv_new, jwk_new = _keypair("new")
        bundle_path = tmp_path / "jwt_bundle.json"

        bundle_path.write_text(json.dumps(_bundle(jwk_old)), encoding="utf-8")
        verifier = SpiffeVerifier(tmp_path, _AUDIENCE)

        old_token = _mint(priv_old, "old", sub=_ARBITER, aud=_AUDIENCE)
        assert verifier.verify(old_token)["sub"] == _ARBITER

        # SPIRE rotates the bundle; spiffe-helper rewrites the file.
        bundle_path.write_text(json.dumps(_bundle(jwk_new)), encoding="utf-8")
        new_token = _mint(priv_new, "new", sub=_ARBITER, aud=_AUDIENCE)
        assert verifier.verify(new_token)["sub"] == _ARBITER
        # The old key is gone from the bundle now → old token rejected.
        with pytest.raises(SpiffeVerificationError):
            verifier.verify(old_token)

    def test_verify_missing_bundle_raises(self, tmp_path: Path):
        verifier = SpiffeVerifier(tmp_path, _AUDIENCE)
        with pytest.raises(SpiffeVerificationError, match="cannot read"):
            verifier.verify("any.token.here")
