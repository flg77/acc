"""Who sent this? — an authenticated sender for the asks that cross a
privilege boundary.

OpenSpec ``20260923-lessons-that-travel`` PA-09 Phase 1 (vault PA-09, the
priority list's row 34).

Phases 8 and 9 moved two worker-initiated control publishes behind the
arbiter: a worker asks, the arbiter acts. Both relays then verified what the
*payload claimed* its sender was — the route relay checks the requesting
role's signed ``can_route`` and the roster — and nothing tied either to the
connection that carried the message. Under NKeys the server knows exactly who
connected; ACC never saw it. So a collective member could ask in another
role's name, and the matrix restriction it was relaying around would mean
nothing.

**What this module does.** The sender signs a canonical form of the payload
with the very key it authenticates its NATS connection with (its NKey seed),
and the receiver verifies that signature against the public half from the key
set ``scripts/acc-nkeys generate`` already writes. No new key material, no new
provisioning, no new dependency: :mod:`acc.nkeys` implements the NKey format
on ``cryptography``, which ACC already ships.

**What it deliberately does not do.**

* It does not prove the sender is *entitled* to what it asks — that stays with
  the receiver (the route relay still checks the signed ``can_route``, the
  roster and the hop cap). This answers *who*, not *may they*.
* It answers *who* as **which key**, which is only as precise as the key set
  is. The eight NKey identities are per-role for the six matrix roles, so a
  signature does pin the role there; a packaged role shares a worker's key and
  a signature says only "some holder of that worker key". That is why the
  route relay's role binding applies solely where the claimed role is itself
  an identity.
* It does not replace the NKey matrix. The server still decides who may
  publish a subject at all; this decides whether a receiver believes a name
  inside a message it was allowed to receive.
* It is **not a second gate on a healthy deployment**. With no key set
  readable a receiver accepts an unverified ask and says so in the log, which
  is exactly today's behaviour — the rollout is: ship signing, distribute the
  key set, and verification turns itself on. A deployment that has the key set
  refuses an ask it cannot verify.

The canonical form is the compact, sorted-key JSON of the payload **without**
the proof field, so a signature covers every other field: change the target
role, the task, the reason, anything, and the signature stops matching.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("acc.wire")

#: Where the proof rides on a payload.  One field, so an older receiver that
#: does not know about it ignores it and behaves exactly as before.
PROOF_FIELD = "sender_proof"

#: Bumped when the canonical form, the algorithm or the proof shape changes.
#: A receiver refuses a revision it does not implement rather than guessing.
#:
#: rev 2 (found by the lighthouse smoke, 2026-09-23): rev 1 named its signer
#: and the verifier looked that name up in the key set.  An agent whose role
#: the matrix does not name signs with a **worker's** seed while calling
#: itself ``orchestrator`` -- a name no key set contains -- so every
#: legitimate ask from the roles Phase 9 exists for was refused the moment a
#: key set was distributed.  The key is now what identifies the signer.
PROOF_SCHEMA_REV = 2

ALG_NKEY_ED25519 = "nkey-ed25519"


class SenderProof(BaseModel):
    """What a signer attaches, and a verifier checks."""

    model_config = ConfigDict(extra="ignore")

    alg: str = ALG_NKEY_ED25519
    schema_rev: int = PROOF_SCHEMA_REV
    #: The signer's NKey **public key** — what actually identifies it.  The
    #: verifier finds this in the key set to learn *which* identity signed,
    #: and then verifies with the key set's copy, never with this one: a
    #: payload that carried both the key and the signature would otherwise
    #: prove only that it is internally consistent.
    public_key: str
    #: A label for logs — what the signer calls its identity.  Not
    #: authoritative, and deliberately not what anything authorises on: an
    #: agent whose role the matrix does not name signs with a worker's key
    #: and calls itself by its role.
    identity: str = ""
    #: The agent that signed, for the audit trail.  Outside the signature,
    #: so nothing may authorise on it either.
    agent_id: str = ""
    signed_at: float = Field(default_factory=time.time)
    signature: str


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The exact bytes a signature covers: the payload minus the proof,
    compact and key-sorted, so both sides build the same message from the
    same dict without agreeing on field order."""
    body = {k: v for k, v in payload.items() if k != PROOF_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sign_payload(
    payload: dict[str, Any],
    *,
    identity: str,
    seed: str,
    agent_id: str = "",
) -> dict[str, Any]:
    """Return *payload* with a :data:`PROOF_FIELD` attached.

    Raises :class:`acc.nkeys.NKeyError` when the seed is not one — the caller
    decides whether that is fatal; :func:`maybe_sign` treats it as "send it
    unsigned", because a missing key must not stop an agent working.
    """
    from acc.nkeys import decode_seed, public_key_of_seed  # noqa: PLC0415

    private = decode_seed(seed)
    body = {k: v for k, v in payload.items() if k != PROOF_FIELD}
    signature = base64.b64encode(private.sign(canonical_bytes(body))).decode("ascii")
    proof = SenderProof(
        public_key=public_key_of_seed(seed), identity=identity,
        agent_id=agent_id, signature=signature,
    )
    return {**body, PROOF_FIELD: proof.model_dump()}


def read_seed(seed_path: str | Path) -> str:
    """The seed text at *seed_path*.  Raises ``OSError`` when unreadable."""
    return Path(seed_path).read_text(encoding="ascii").strip()


def load_public_keys(path: str | Path) -> dict[str, str]:
    """The ``{identity: U…}`` key set, or ``{}`` when there is none to read.

    An empty map is the "cannot verify" signal, not an error: it is the state
    of every deployment that has not distributed the key set yet.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="ascii"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("wire: key set at %s unreadable: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("wire: key set at %s is not an object", path)
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}


def default_public_keys_path(seed_path: str | Path) -> Path:
    """Where ``acc-nkeys generate`` leaves the key set: beside the seeds."""
    return Path(seed_path).parent / "public_keys.json"


def identity_of_key(public_key: str, public_keys: dict[str, str]) -> str:
    """Which key-set entry holds *public_key*, or ``""``.

    This is the direction that matters: the signer proves possession of a
    key, and the key set says whose it is.  Asking the signer to name itself
    and then looking that name up is how rev 1 refused every ask from a role
    the matrix does not name.
    """
    target = (public_key or "").strip()
    if not target:
        return ""
    for identity, key in public_keys.items():
        if key.strip() == target:
            return identity
    return ""


def verify_payload(
    payload: dict[str, Any],
    public_keys: dict[str, str],
    *,
    expected_identity: str = "",
) -> str:
    """``""`` when the sender is proven, else why it is not.

    *expected_identity* lets a receiver additionally require that the key
    which signed belongs to a particular identity — checked against the one
    the **key set** resolves, never against the label in the payload.
    """
    from acc.nkeys import NKeyError, decode_public  # noqa: PLC0415

    raw = payload.get(PROOF_FIELD)
    if not isinstance(raw, dict):
        return "unsigned"
    try:
        proof = SenderProof.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        return f"malformed proof: {str(exc).splitlines()[0]}"
    if proof.alg != ALG_NKEY_ED25519:
        return f"unsupported algorithm {proof.alg!r}"
    if proof.schema_rev != PROOF_SCHEMA_REV:
        return f"unsupported proof schema_rev {proof.schema_rev}"
    identity = identity_of_key(proof.public_key, public_keys)
    if not identity:
        return "signing key is not in the key set"
    if expected_identity and identity != expected_identity:
        return f"signed by {identity!r}, expected {expected_identity!r}"
    try:
        signature = base64.b64decode(proof.signature, validate=True)
    except Exception:  # noqa: BLE001
        return "signature is not base64"
    try:
        # The key set's copy, not the payload's: a payload carrying both the
        # key and the signature over itself proves only self-consistency.
        decode_public(public_keys[identity]).verify(signature, canonical_bytes(payload))
    except NKeyError as exc:
        return f"key set entry for {identity!r} is not an NKey: {exc}"
    except Exception:
        # InvalidSignature, and anything else cryptography raises.  The
        # message says nothing about the key: a verifier that explains HOW a
        # signature failed is a verifier that helps someone forge one.
        return f"signature does not verify for identity {identity!r}"
    return ""


def signer_identity(payload: dict[str, Any]) -> str:
    """The label a payload puts on its signer, for logging.  Says nothing
    about whether it verified, and nothing may authorise on it -- use
    :func:`identity_of_key` against the key set for that."""
    raw = payload.get(PROOF_FIELD)
    return str(raw.get("identity", "")) if isinstance(raw, dict) else ""
