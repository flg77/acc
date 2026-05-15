"""Cross-mode SPIFFE compatibility e2e (proposal 012 PR-4).

The locked requirement of proposal 012 is **bi-directional
compatibility** between rhoai and edge: a ROLE_UPDATE signed on one
side must verify on the other.  A true test would need two real
clusters with nested / federated SPIRE; that is the operator's
manual acceptance step.

This file gives the *crypto-level* e2e that CI can run unattended.
It models each trust topology with synthetic SPIRE keypairs, mints
JWT-SVIDs exactly as a SPIRE workload API would, and verifies them
through the production ``acc.spiffe_verify`` path — covering all six
message-flow directions from proposal 012 §11's compatibility
matrix plus the offline-survival timeline.

No cluster, no network — runs in milliseconds, unskipped.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from acc.spiffe_offline import (
    ACTION_DEGRADE,
    ACTION_ROTATE,
    STATE_FRESH,
    OfflineBundleMonitor,
)
from acc.spiffe_verify import SpiffeVerificationError, verify_jwt_svid

_AUD = "acc-role-update"


# ---------------------------------------------------------------------------
# Synthetic SPIRE — a trust domain is a keypair; a bundle is its JWKS.
# ---------------------------------------------------------------------------


class _SpireDomain:
    """A synthetic SPIRE trust domain: one signing keypair + the
    matching public JWK.  Mints JWT-SVIDs for workloads in the
    domain; its ``jwk`` goes into a trust bundle for verifiers."""

    def __init__(self, kid: str) -> None:
        self.kid = kid
        self._priv = ec.generate_private_key(ec.SECP256R1())
        self.jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(self._priv.public_key()))
        self.jwk["kid"] = kid

    def mint(self, sub: str, *, aud: str = _AUD, exp_offset: float = 300.0) -> str:
        """Mint a JWT-SVID for the workload identified by *sub*."""
        now = time.time()
        return jwt.encode(
            {"sub": sub, "aud": aud, "iat": int(now), "exp": int(now + exp_offset)},
            self._priv, algorithm="ES256", headers={"kid": self.kid},
        )


def _bundle(*domains: _SpireDomain) -> dict:
    """A trust bundle (JWKS) covering one or more domains' keys."""
    return {"keys": [d.jwk for d in domains]}


# ===========================================================================
# Nested topology — rhoai parent + edge sites share ONE trust domain.
# All identities chain to the parent's single root, so one shared
# bundle verifies every direction.
# ===========================================================================


class TestNestedCompatibility:
    """Five of the six matrix directions live under nested topology —
    the shared trust domain is exactly what makes them work."""

    def setup_method(self) -> None:
        # One root for the whole nested deployment.
        self.root = _SpireDomain("nested-root")
        self.shared = _bundle(self.root)
        self.td = "acc-prod.example.com"

    def test_rhoai_to_rhoai(self):
        token = self.root.mint(f"spiffe://{self.td}/role/arbiter")
        claims = verify_jwt_svid(token, self.shared, _AUD)
        assert claims["sub"] == f"spiffe://{self.td}/role/arbiter"

    def test_rhoai_to_edge(self):
        # rhoai arbiter mints; an edge agent verifies against its
        # cached copy of the (shared) parent bundle.
        token = self.root.mint(f"spiffe://{self.td}/role/arbiter")
        claims = verify_jwt_svid(token, self.shared, _AUD)
        assert claims["sub"].endswith("/role/arbiter")

    def test_edge_to_rhoai(self):
        # An edge arbiter's SPIFFE ID carries the /edge/<site>/ segment.
        token = self.root.mint(f"spiffe://{self.td}/edge/factory-a/role/arbiter")
        claims = verify_jwt_svid(token, self.shared, _AUD)
        assert "/edge/factory-a/" in claims["sub"]

    def test_edge_to_edge_same_site(self):
        token = self.root.mint(f"spiffe://{self.td}/edge/factory-a/role/arbiter")
        verify_jwt_svid(token, self.shared, _AUD,
                        expected_spiffe_id=f"spiffe://{self.td}/edge/factory-a/role/arbiter")

    def test_edge_to_edge_different_nested_sites(self):
        # factory-a's arbiter → factory-b's agent.  Same shared root,
        # so the cross-site token verifies.
        token = self.root.mint(f"spiffe://{self.td}/edge/factory-a/role/arbiter")
        claims = verify_jwt_svid(token, self.shared, _AUD)
        assert claims["sub"] == f"spiffe://{self.td}/edge/factory-a/role/arbiter"

    def test_strict_subject_binding_rejects_wrong_site(self):
        # With arbiter_spiffe_id pinned to factory-a, a factory-b
        # token is rejected even though it chains to the same root.
        token = self.root.mint(f"spiffe://{self.td}/edge/factory-b/role/arbiter")
        with pytest.raises(SpiffeVerificationError, match="not the expected arbiter"):
            verify_jwt_svid(token, self.shared, _AUD,
                            expected_spiffe_id=f"spiffe://{self.td}/edge/factory-a/role/arbiter")


# ===========================================================================
# Federated topology — each edge owns a DISTINCT trust domain.
# Cross-trust works only after a bundle exchange.
# ===========================================================================


class TestFederatedCompatibility:
    def setup_method(self) -> None:
        self.factory_a = _SpireDomain("factory-a-root")
        self.factory_b = _SpireDomain("factory-b-root")

    def test_federated_cross_trust_after_bundle_exchange(self):
        # factory-a's arbiter → factory-b's agent.  factory-b has
        # federated, so its bundle includes factory-a's key.
        token = self.factory_a.mint("spiffe://factory-a.acc.local/role/arbiter")
        federated_bundle = _bundle(self.factory_b, self.factory_a)
        claims = verify_jwt_svid(token, federated_bundle, _AUD)
        assert claims["sub"] == "spiffe://factory-a.acc.local/role/arbiter"

    def test_federation_is_required(self):
        # Without the bundle exchange, factory-b only has its own key
        # → factory-a's token is rejected.  This is the negative
        # control proving federation is actually doing the work.
        token = self.factory_a.mint("spiffe://factory-a.acc.local/role/arbiter")
        own_bundle_only = _bundle(self.factory_b)
        with pytest.raises(SpiffeVerificationError):
            verify_jwt_svid(token, own_bundle_only, _AUD)

    def test_federated_both_directions(self):
        # Symmetric: b→a works once a's bundle includes b.
        token = self.factory_b.mint("spiffe://factory-b.acc.local/role/arbiter")
        a_federated = _bundle(self.factory_a, self.factory_b)
        claims = verify_jwt_svid(token, a_federated, _AUD)
        assert claims["sub"] == "spiffe://factory-b.acc.local/role/arbiter"


# ===========================================================================
# Offline survival timeline.
# ===========================================================================


class TestOfflineTimeline:
    """An edge partitioned from its parent: the bundle ages, then
    offline_action fires."""

    def _bundle_file(self, tmp_path: Path, age_h: float) -> Path:
        import os
        path = tmp_path / "jwt_bundle.json"
        path.write_text('{"keys": []}', encoding="utf-8")
        old = time.time() - age_h * 3600
        os.utime(path, (old, old))
        return path

    def test_fresh_within_window(self, tmp_path: Path):
        self._bundle_file(tmp_path, age_h=10.0)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        assert mon.check() == STATE_FRESH

    def test_degrade_past_window(self, tmp_path: Path):
        self._bundle_file(tmp_path, age_h=80.0)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        assert mon.check() == ACTION_DEGRADE

    def test_rotate_past_window(self, tmp_path: Path):
        # nested sites configure rotate — the edge SPIRE re-signs and
        # the agent keeps serving.
        self._bundle_file(tmp_path, age_h=80.0)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_ROTATE)
        assert mon.check() == ACTION_ROTATE

    def test_partition_recovery(self, tmp_path: Path):
        # Stale → spiffe-helper reconnects and rewrites a fresh bundle
        # → monitor reports fresh again, no restart.
        bundle = self._bundle_file(tmp_path, age_h=80.0)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        assert mon.check() == ACTION_DEGRADE
        bundle.write_text('{"keys": []}', encoding="utf-8")  # refreshed now
        assert mon.check() == STATE_FRESH
