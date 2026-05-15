"""ACC SPIFFE JWT-SVID verification (proposal 011 PR-4).

The agent-side counterpart to the operator's SPIFFE provisioning
(011 PR-2/PR-3).  When ``security.signing_mode`` is ``spiffe``, a
ROLE_UPDATE carries the arbiter's **JWT-SVID** in its ``signature``
field instead of an Ed25519 signature.  This module verifies that
JWT-SVID against the SPIRE trust bundle that the ``spiffe-helper``
sidecar materialises onto disk.

What a JWT-SVID proves
----------------------

A JWT-SVID is an *identity* token, not a content signature.  Verifying
one proves:

* the token was minted by the collective's SPIRE (signature chains to
  the trust bundle),
* it was issued for audience ``acc-role-update`` (``aud`` claim),
* the bearer is the expected arbiter (``sub`` claim — the arbiter's
  SPIFFE ID),
* it has not expired (``exp`` claim).

It does **not** bind to the ROLE_UPDATE's ``role_definition`` content.
Content integrity stays the job of the existing
``approver_id == expected_arbiter`` check + role-version monotonicity
in :mod:`acc.role_store`.  The SPIFFE upgrade replaces "trust a static
Ed25519 key forever" with "trust a SPIRE-attested, short-lived,
rotat­able identity" — a real improvement to the *key-management*
posture even though the content-binding guarantees are unchanged.

Dependency
----------

``PyJWT`` is a declared dependency (pyproject.toml) — small,
pure-Python, and it reuses the ``cryptography`` dep ACC already
ships.  It is nonetheless imported lazily so a stripped-down
deployment that somehow lacks it fails with a clear, actionable
error on the first ``signing_mode: spiffe`` verification rather
than at agent import time.

Design reference: proposal 011 §5 step 4.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("acc.spiffe_verify")


# Default file names the spiffe-helper sidecar writes (must match
# operator/internal/reconcilers/collective/spiffe_sidecar.go).
DEFAULT_JWT_SVID_FILE = "jwt_svid.token"
DEFAULT_JWT_BUNDLE_FILE = "jwt_bundle.json"

# Clock-skew tolerance for exp/iat validation.  Matches the go-jwt
# default; covers minor drift between the arbiter and agent nodes.
CLOCK_SKEW_S = 60


class SpiffeVerificationError(Exception):
    """Raised when a JWT-SVID fails verification.

    Operator-readable — the message is safe to log + surface in the
    role audit chain.
    """


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def load_jwt_svid(mount_path: str | Path,
                  file_name: str = DEFAULT_JWT_SVID_FILE) -> str:
    """Read the arbiter's JWT-SVID token from the spiffe-helper mount.

    Used by the *arbiter* to populate a ROLE_UPDATE's ``signature``
    field.  Returns the raw compact-JWT string.
    """
    path = Path(mount_path) / file_name
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SpiffeVerificationError(
            f"cannot read JWT-SVID at {path}: {exc}"
        ) from exc
    if not token:
        raise SpiffeVerificationError(f"JWT-SVID at {path} is empty")
    return token


def load_jwt_bundle(mount_path: str | Path,
                    file_name: str = DEFAULT_JWT_BUNDLE_FILE) -> dict[str, Any]:
    """Read the SPIRE JWT trust bundle (a JWKS document) from disk.

    The bundle is the set of public keys the agent uses to verify a
    JWT-SVID.  spiffe-helper writes it when its config sets
    ``jwt_bundle_file_name``.
    """
    path = Path(mount_path) / file_name
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpiffeVerificationError(
            f"cannot read JWT bundle at {path}: {exc}"
        ) from exc
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpiffeVerificationError(
            f"JWT bundle at {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(bundle, dict) or "keys" not in bundle:
        raise SpiffeVerificationError(
            f"JWT bundle at {path} is not a JWKS document (no 'keys')"
        )
    return bundle


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _import_jwt():
    """Lazy-import PyJWT, raising an operator-readable error if absent."""
    try:
        import jwt  # noqa: PLC0415
        from jwt import algorithms  # noqa: PLC0415
        return jwt, algorithms
    except ImportError as exc:
        raise SpiffeVerificationError(
            "PyJWT is required for signing_mode=spiffe but is not "
            "installed.  Install with `pip install pyjwt` or switch "
            "signing_mode back to 'ed25519' in acc-config.yaml."
        ) from exc


def _key_for_kid(bundle: dict[str, Any], kid: str, algorithms_mod: Any) -> Any:
    """Resolve the public key in *bundle* whose ``kid`` matches.

    SPIRE JWT bundles are JWKS — a list of JWK dicts.  Each carries a
    ``kty`` (EC or RSA) and a ``kid``.  We pick the key whose ``kid``
    matches the JWT header, then materialise it via PyJWT's algorithm
    helpers.
    """
    for jwk in bundle.get("keys", []):
        if jwk.get("kid") != kid:
            continue
        kty = jwk.get("kty", "")
        jwk_json = json.dumps(jwk)
        if kty == "EC":
            return algorithms_mod.ECAlgorithm.from_jwk(jwk_json)
        if kty == "RSA":
            return algorithms_mod.RSAAlgorithm.from_jwk(jwk_json)
        raise SpiffeVerificationError(
            f"unsupported JWK key type {kty!r} in trust bundle"
        )
    raise SpiffeVerificationError(
        f"no key in the trust bundle matches JWT kid {kid!r}"
    )


def verify_jwt_svid(
    token: str,
    bundle: dict[str, Any],
    expected_audience: str,
    expected_spiffe_id: Optional[str] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Verify a JWT-SVID and return its claims.

    Args:
        token: the compact JWT-SVID string.
        bundle: the SPIRE JWT trust bundle (JWKS dict).
        expected_audience: required value in the ``aud`` claim.
        expected_spiffe_id: when given, the ``sub`` claim must equal
            it (the arbiter's SPIFFE ID).  When ``None``, ``sub`` is
            returned but not enforced — the caller may do its own
            identity check.
        now: optional unix-time override for tests.

    Returns:
        The verified claims dict.

    Raises:
        SpiffeVerificationError: on any failure — bad signature,
            wrong audience, wrong subject, expiry, malformed token.
    """
    jwt_mod, algorithms_mod = _import_jwt()

    # 1. Read the unverified header to find the signing-key id.
    try:
        header = jwt_mod.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        raise SpiffeVerificationError(
            f"JWT-SVID header is unreadable: {exc}"
        ) from exc
    kid = header.get("kid", "")
    alg = header.get("alg", "")
    if not kid:
        raise SpiffeVerificationError("JWT-SVID header has no 'kid'")
    if alg in ("", "none"):
        raise SpiffeVerificationError(
            f"JWT-SVID uses unacceptable alg {alg!r}"
        )

    # 2. Resolve the verification key from the trust bundle.
    key = _key_for_kid(bundle, kid, algorithms_mod)

    # 3. Verify signature + audience + expiry in one decode call.
    leeway = CLOCK_SKEW_S
    decode_kwargs: dict[str, Any] = {
        "algorithms": [alg],
        "audience": expected_audience,
        "leeway": leeway,
        "options": {"require": ["exp", "sub", "aud"]},
    }
    try:
        claims = jwt_mod.decode(token, key, **decode_kwargs)
    except Exception as exc:  # noqa: BLE001
        # PyJWT raises a family of exceptions (ExpiredSignature,
        # InvalidAudience, InvalidSignature, …).  Collapse to one
        # operator-readable error.
        raise SpiffeVerificationError(
            f"JWT-SVID verification failed: {exc}"
        ) from exc

    # 4. Enforce the subject (arbiter SPIFFE ID) when requested.
    sub = claims.get("sub", "")
    if expected_spiffe_id is not None and sub != expected_spiffe_id:
        raise SpiffeVerificationError(
            f"JWT-SVID subject {sub!r} is not the expected arbiter "
            f"{expected_spiffe_id!r}"
        )

    # 5. Belt-and-braces explicit expiry check (decode already does
    #    this, but an old PyJWT + a missing 'exp' require option could
    #    slip through — keep it deterministic for tests via `now`).
    exp = claims.get("exp")
    if exp is not None:
        current = now if now is not None else time.time()
        if current - leeway > float(exp):
            raise SpiffeVerificationError("JWT-SVID has expired")

    logger.debug(
        "spiffe_verify: JWT-SVID accepted (sub=%s aud=%s)",
        sub, expected_audience,
    )
    return claims


class SpiffeVerifier:
    """Stateful wrapper that caches the trust bundle between calls.

    The bundle file is re-read on each :meth:`verify` so that
    spiffe-helper's periodic bundle refresh is picked up without an
    agent restart.  Construction is cheap; agents hold one instance.

    Args:
        svid_mount_path: the directory spiffe-helper writes into
            (``security.spiffe.svid_mount_path``).
        jwt_audience: the required ``aud`` claim
            (``security.spiffe.jwt_audience``).
    """

    def __init__(self, svid_mount_path: str | Path, jwt_audience: str) -> None:
        self._mount = Path(svid_mount_path)
        self._audience = jwt_audience

    def verify(
        self,
        token: str,
        expected_spiffe_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Verify *token* against the on-disk trust bundle.

        Raises :class:`SpiffeVerificationError` on any failure.
        """
        bundle = load_jwt_bundle(self._mount)
        return verify_jwt_svid(
            token,
            bundle,
            expected_audience=self._audience,
            expected_spiffe_id=expected_spiffe_id,
            now=now,
        )
