"""Worker-pool role-assign signer / verifier (PR-J, D-001).

The arbiter's reconcile loop promotes a DORMANT worker into a named
role by publishing a ``SIG_ROLE_ASSIGN`` on
``acc.<cid>.role_assign``.  The payload carries an Ed25519 signature
over the canonical (sorted, no-whitespace) JSON of:

    {
        "approver_id": "<arbiter agent_id>",
        "target_agent_id": "<dormant worker agent_id>",
        "role_definition": { <RoleDefinitionConfig.model_dump()> },
        "cluster_id": "<optional>",
        "purpose": "<optional>",
    }

This binds the signature to ALL four security-relevant fields:

* ``approver_id``    — prevents re-using a signed payload with a
  different claimed approver.
* ``target_agent_id`` — prevents redirecting a signature meant for
  worker A onto worker B.  Without this an attacker who sniffed a
  valid ROLE_ASSIGN on the bus could publish it again targeting a
  different dormant worker.
* ``role_definition`` — prevents swapping in a richer role (more
  ``allowed_actions``, broader ``allowed_skills``) than the arbiter
  actually signed for.
* ``cluster_id`` + ``purpose`` — operator-visible metadata; included
  in the signed envelope so the TUI's "what was approved" audit
  is faithful.

Keeps the worker-pool boot path simple: the dormant agent verifies
the signature against the registered arbiter verify-key (same key
used by ``acc.role_store`` for ROLE_UPDATE), and on a pass loads
the role definition into a CognitiveCore.

Verification failures are wrapped in :class:`RoleAssignRejectedError`
so the agent handler can surface a single exception type instead of
discriminating across cryptography errors / base64 errors / key
loading errors.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger("acc.role_assign")


class RoleAssignRejectedError(ValueError):
    """A ROLE_ASSIGN payload failed validation.  Carries a short
    human-readable reason; the agent handler surfaces it in a
    WARNING log line so an operator scrolling ``acc-agent-*`` logs
    can see exactly why a promotion didn't happen."""


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def _canonical_signed_message(
    *,
    approver_id: str,
    target_agent_id: str,
    role_definition: dict,
    cluster_id: str = "",
    purpose: str = "",
) -> bytes:
    """Return the canonical UTF-8 JSON the signer / verifier both
    operate on.  Sorted keys, no whitespace, stable across
    Python versions."""
    return json.dumps(
        {
            "approver_id": approver_id,
            "target_agent_id": target_agent_id,
            "role_definition": role_definition,
            "cluster_id": cluster_id,
            "purpose": purpose,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


# ---------------------------------------------------------------------------
# Signer (arbiter-side)
# ---------------------------------------------------------------------------


def sign_role_assign(
    *,
    approver_id: str,
    target_agent_id: str,
    role_definition: dict,
    cluster_id: str = "",
    purpose: str = "",
    private_key_b64: str,
) -> dict:
    """Build a signed ROLE_ASSIGN payload ready for NATS publish.

    Args:
        approver_id: The arbiter's agent_id.
        target_agent_id: The dormant worker this assignment targets.
        role_definition: ``RoleDefinitionConfig.model_dump()``.
        cluster_id: Operator-supplied cluster grouping.
        purpose: Operator-supplied purpose string.
        private_key_b64: Base64-encoded raw 32-byte Ed25519 private
            key.  The arbiter holds this; it never leaves the
            arbiter process.

    Returns:
        A dict matching the wire shape documented in
        :func:`acc.signals.subject_role_assign`, ready to pass to
        ``signaling.publish``.
    """
    try:
        key_bytes = base64.b64decode(private_key_b64)
        private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    except Exception as exc:
        raise RoleAssignRejectedError(
            f"arbiter signing key invalid: {exc}",
        ) from exc

    msg = _canonical_signed_message(
        approver_id=approver_id,
        target_agent_id=target_agent_id,
        role_definition=role_definition,
        cluster_id=cluster_id,
        purpose=purpose,
    )
    signature_bytes = private_key.sign(msg)
    return {
        "signal_type": "ROLE_ASSIGN",
        "approver_id": approver_id,
        "target_agent_id": target_agent_id,
        "role_definition": role_definition,
        "cluster_id": cluster_id,
        "purpose": purpose,
        "signature": base64.b64encode(signature_bytes).decode("ascii"),
    }


# ---------------------------------------------------------------------------
# Verifier (worker-side)
# ---------------------------------------------------------------------------


def verify_role_assign(payload: dict, *, verify_key_b64: str) -> None:
    """Verify a signed ROLE_ASSIGN payload.  Raises
    :class:`RoleAssignRejectedError` on any failure (bad key,
    missing field, tampered payload, wrong key).

    The dormant agent calls this from its
    ``_handle_role_assign`` before touching anything else — a
    rejected payload never leads to a CognitiveCore boot.
    """
    if not isinstance(payload, dict):
        raise RoleAssignRejectedError("payload is not a dict")

    approver_id = str(payload.get("approver_id", ""))
    target_agent_id = str(payload.get("target_agent_id", ""))
    role_definition = payload.get("role_definition") or {}
    cluster_id = str(payload.get("cluster_id", ""))
    purpose = str(payload.get("purpose", ""))
    signature = str(payload.get("signature", ""))

    if not approver_id:
        raise RoleAssignRejectedError("approver_id is empty")
    if not target_agent_id:
        raise RoleAssignRejectedError("target_agent_id is empty")
    if not isinstance(role_definition, dict) or not role_definition:
        raise RoleAssignRejectedError("role_definition is empty / non-dict")
    if not signature:
        raise RoleAssignRejectedError("signature is empty")
    if not verify_key_b64:
        raise RoleAssignRejectedError("verify_key not configured")

    try:
        key_bytes = base64.b64decode(verify_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
    except Exception as exc:
        raise RoleAssignRejectedError(
            f"cannot load arbiter verify key: {exc}",
        ) from exc

    msg = _canonical_signed_message(
        approver_id=approver_id,
        target_agent_id=target_agent_id,
        role_definition=role_definition,
        cluster_id=cluster_id,
        purpose=purpose,
    )

    try:
        sig_bytes = base64.b64decode(signature)
    except Exception as exc:
        raise RoleAssignRejectedError(
            f"signature is not valid base64: {exc}",
        ) from exc

    try:
        public_key.verify(sig_bytes, msg)
    except InvalidSignature as exc:
        raise RoleAssignRejectedError(
            "signature does not match payload (tampered or wrong key)",
        ) from exc


# ---------------------------------------------------------------------------
# Test convenience
# ---------------------------------------------------------------------------


def generate_keypair_b64() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair, both Base64-encoded.

    Returned tuple is ``(private_key_b64, public_key_b64)``.  Used by
    tests; the production arbiter loads keys from disk via
    :mod:`acc.config`'s security block.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption, PublicFormat,
    )
    priv_raw = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    pub_raw = public_key.public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_raw).decode("ascii"),
        base64.b64encode(pub_raw).decode("ascii"),
    )
