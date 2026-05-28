"""A2A Agent Card signing + verification.

OpenSpec: ``openspec/changes/20260527-a2a-agent-interop/`` (Phase 5).
Docs: ``docs/a2a-interop.md``.  Sibling change:
``20260527-agentcard-discovery`` (the operator-side discovery label whose
``targetRef`` identity binding is attested by the signing here).

ACC reuses the **JWT-SVID** issued by SPIRE (proposal 011 / PR-4 already wires
SPIRE workload-API attestation into the agent pod via the ``spiffe-helper``
sidecar — see ``acc/spiffe.py``).  The signing flow is therefore minimal:

- The SPIRE workload API issues a short-lived JWT-SVID for the workload (signed
  by SPIRE's CA, ``sub`` = the SPIFFE ID like ``spiffe://<td>/role/<id>``).
- The agent reads the JWT-SVID from a file path that ``spiffe-helper`` keeps
  rotated, and ships it alongside the agent card.
- A peer **verifies** the JWT-SVID against the issuing SPIRE's JWKS bundle,
  asserts the SPIFFE ID's trust domain matches the expected one (and any
  expected audience claim), and only then trusts the card.

No new dependencies: ``pyjwt`` is already in the core deps for the wider
``signing_mode=spiffe`` work; ``cryptography`` provides PEM parsing.  Plain
HTTP for Phase 1b/2 stays — Phase 5 layers signing on top so a peer can
attest the card *content* even before TLS at the transport layer arrives.

This module is **pure protocol**:

- :func:`sign_card` wraps a card dict with a JWT-SVID it does **not** itself
  produce — SPIRE owns key material.  Callers provide the JWT-SVID string
  (typically by reading the file path :func:`read_jwt_svid_file` returns).
- :func:`verify_signed_card` validates a signed card against an issuer key
  + an expected trust domain (and optional audience) and returns the card.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Card-card scheme that goes into the agent card's authentication.schemes
# --------------------------------------------------------------------------

# A peer reads this from the card's ``authentication.schemes`` to know how
# to verify a signed envelope.  The trust domain pins the issuer and is the
# key claim the peer enforces.
def spire_x5c_scheme(trust_domain: str) -> dict[str, Any]:
    """Return the ``authentication.schemes`` entry for SPIRE-issued JWT-SVID
    card signing.  Populated into the card when signing is configured.

    Once SPIRE-issued X.509-SVIDs replace the JWT-SVID transport — or when
    we serve TLS using those X.509-SVIDs directly — additional schemes get
    appended here; until then, peers see one scheme + know to verify the
    accompanying JWT-SVID."""
    return {
        "scheme": "spire-jwt-svid",
        "trustDomain": trust_domain,
        # Echo the OpenSpec id so a peer can correlate to the change defining
        # the verification contract.
        "openSpec": "20260527-a2a-agent-interop",
    }


# --------------------------------------------------------------------------
# Signing — wrap a card with its JWT-SVID
# --------------------------------------------------------------------------


def sign_card(card: dict[str, Any], jwt_svid: str) -> dict[str, Any]:
    """Wrap a card dict with its accompanying JWT-SVID.

    ACC does NOT produce key material here — SPIRE does.  ``jwt_svid`` is
    expected to be a JWS issued by the cluster SPIRE for *this* workload,
    typically read from the file ``spiffe-helper`` keeps rotated (see
    :func:`read_jwt_svid_file`).

    Returns a small envelope dict with two keys: ``"card"`` (the
    canonical-shape dict the peer reads) and ``"svid"`` (the JWT-SVID the
    peer verifies).  Caller is free to serialise / transport however; the
    A2A server in Phase 5 returns this envelope from ``/.well-known/
    agent-card.json`` when signing is enabled.
    """
    if not isinstance(jwt_svid, str) or not jwt_svid.strip():
        raise ValueError("sign_card requires a non-empty JWT-SVID string")
    return {"card": card, "svid": jwt_svid}


def read_jwt_svid_file(path: str | Path) -> str:
    """Read the JWT-SVID from a ``spiffe-helper``-rotated file.

    A tiny helper — split out so the agent's wiring is just:
    ``signed = sign_card(card, read_jwt_svid_file(config.spiffe.jwt_svid_path))``.
    Raises ``FileNotFoundError`` / ``OSError`` as the I/O layer normally would,
    so the caller can surface a meaningful operator message.
    """
    return Path(path).read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------
# Verification — confirm a peer's signed card came from a trusted SPIFFE ID
# --------------------------------------------------------------------------


class CardVerificationError(Exception):
    """A signed card failed verification (bad signature, wrong trust domain,
    expired, missing claims, malformed envelope).  Distinct exception so the
    A2A client treats it as a security failure — *do not retry* on NATS."""


def verify_signed_card(
    signed: dict[str, Any],
    *,
    issuer_key: Any,
    expected_trust_domain: str,
    expected_audience: str | list[str] | None = None,
    algorithms: list[str] | None = None,
    leeway_s: int = 30,
) -> dict[str, Any]:
    """Verify a signed agent card and return the unwrapped card dict.

    Parameters
    ----------
    signed:
        Envelope from :func:`sign_card` — ``{"card": <dict>, "svid": <jwt>}``.
    issuer_key:
        Public key (PEM bytes / cryptography key object / PyJWKClient-style
        key) the SPIRE issuer uses to sign JWT-SVIDs.  In production this is
        fetched from the SPIRE JWKS endpoint (see ``acc/spiffe.py``).
    expected_trust_domain:
        Required.  The SPIFFE trust domain the caller expects (e.g.
        ``"acc-prod.example.com"``).  The JWT's ``sub`` claim must be a
        ``spiffe://<expected_trust_domain>/...`` URI.
    expected_audience:
        Optional ``aud`` claim to enforce.  When set, the JWT-SVID must
        declare this audience (the receiver's expected SPIFFE ID).
    algorithms:
        Allowed JWT algorithms.  Defaults to SPIRE's RS256 + ES256 set.
    leeway_s:
        Clock-skew tolerance for ``exp``/``nbf``/``iat`` (default 30s).

    Returns
    -------
    The card dict (``signed["card"]``) when verification succeeds.

    Raises
    ------
    :class:`CardVerificationError` on any failure — bad signature, expired,
    wrong trust domain, missing claims, malformed envelope.  Callers treat
    this as a security denial (do NOT fall back to a different transport).
    """
    if not isinstance(signed, dict) or "card" not in signed or "svid" not in signed:
        raise CardVerificationError(
            "envelope must be {'card': <dict>, 'svid': <jwt>}"
        )
    card = signed["card"]
    svid = signed["svid"]
    if not isinstance(card, dict):
        raise CardVerificationError("envelope 'card' is not a dict")
    if not isinstance(svid, str) or not svid.strip():
        raise CardVerificationError("envelope 'svid' is empty")

    try:
        import jwt as _jwt  # noqa: PLC0415 — pyjwt is in core deps
    except ImportError as exc:
        raise CardVerificationError(f"pyjwt not available: {exc}") from exc

    algs = algorithms or ["RS256", "ES256"]
    try:
        claims = _jwt.decode(
            svid,
            issuer_key,
            algorithms=algs,
            audience=expected_audience,
            leeway=leeway_s,
            options={"require": ["exp", "iat", "sub"]},
        )
    except _jwt.InvalidTokenError as exc:
        raise CardVerificationError(f"JWT-SVID invalid: {exc}") from exc

    sub = claims.get("sub", "")
    expected_prefix = f"spiffe://{expected_trust_domain}/"
    if not isinstance(sub, str) or not sub.startswith(expected_prefix):
        raise CardVerificationError(
            f"SPIFFE id {sub!r} is not in trust domain {expected_trust_domain!r}"
        )

    return card


def spiffe_id_trust_domain(spiffe_id: str) -> str:
    """Extract the trust domain from a SPIFFE id (e.g. ``spiffe://<td>/x/y``).

    Tiny helper — split out so the caller can do
    ``spiffe_id_trust_domain(claims["sub"])`` for audit logging without
    repeating the parse in two places."""
    if not spiffe_id.startswith("spiffe://"):
        return ""
    rest = spiffe_id[len("spiffe://"):]
    return rest.split("/", 1)[0] if "/" in rest else rest
