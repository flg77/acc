"""Policy layer — reward harness + θ schema (seam for SIP-P1).

Proposal: ``20260530-acc-self-improvement-policy-gradient`` Phase 1 hook
points, landed alongside the gatekeeper's Phase 1 so SIP-P1 has a
ready-made plug-in surface.

**This module does NOT learn anything yet.** Phase 1 ships only the
plumbing:

- :class:`RewardHarness` — subscribes to the four reward sources
  identified in the SIP proposal (EVAL_OUTCOME, oversight queue
  verdicts, drift updates, Cat-C denials) and logs each reward to a
  structured logger.  No aggregation, no θ vector, no policy updates.
- :func:`is_enabled` — the harness is *opt-in* via the
  ``ACC_POLICY_LAYER_ENABLED`` environment variable.  Default off so the
  Phase 1 landing has zero runtime side-effects on any current
  deployment.
- A pinned θ schema constant so future phases (and tests) can build on
  a stable shape.

Why ship the skeleton in Phase 1 of the *gatekeeper*: the agent task
loop, the cognitive core, and the oversight queue all touch reward
sources.  Bolting the harness on later would require revisiting every
one of those sites.  Defining the seam now lets SIP-P1 add the
subscriptions without a second pass.

See the integration & sequencing note for the exact sequence:
[[20260530-aoa-and-self-improve — integration & sequencing]].
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("acc.policy_layer")


# Default policy vector (θ_0).  Phase 1 ships the schema; values are
# the per-role.yaml defaults that the bandit (SIP-P2) will tune over.
# Pinned so tests + future migrations agree on the field set.
DEFAULT_POLICY_VECTOR: dict[str, float] = {
    # When to delegate to a specialist vs answer directly.
    # Higher = stricter routing (only route on high-confidence signals).
    "route_confidence_threshold": 0.65,
    # When to ask for a new dormant worker.
    # Higher = wait for clearer demand before spawning capacity.
    "spawn_threshold": 0.80,
    # Cross-collective delegation cutoff (domain similarity).
    # Higher = only delegate on a strong domain match.
    "delegate_domain_match": 0.55,
    # Top-K depth on the memory hot-cache retrieval.
    "memory_top_k": 5.0,
    # Target reasoning-step count when reasoning_trace is enabled.
    "reasoning_depth_target": 4.0,
}


# Reward source kinds — mirrors the rows in the SIP proposal's reward
# stream definition.  Used as the ``kind`` field on harness emits.
REWARD_EVAL_OUTCOME = "eval_outcome"
REWARD_OPERATOR_APPROVAL = "operator_approval"
REWARD_TASK_CANCEL = "task_cancel"
REWARD_CAT_C_DENIAL = "cat_c_denial"
REWARD_DRIFT_OVERAGE = "drift_overage"


# SIP-P1 — per-reward-kind EWMA smoothing factor.  Reward values
# arrive sparsely (one per eval / queue verdict / Cat-A trip), so a
# moderately high α (recent reward weighted ~30%) gives a responsive
# but stable running estimate.  SIP-P2 will revisit when the bandit
# update lands; for now the EWMA exists to *observe* the running
# reward profile, not to drive any policy update.
_DEFAULT_EWMA_ALPHA = 0.30


# Per-reward-kind sign convention.  Phase 1's logged rewards become
# numeric via this mapping when the payload doesn't carry an explicit
# score: positive signals (approval, eval outcome) → +1.0; negative
# signals (Cat-C denial, task cancel) → -1.0.  Eval outcomes whose
# payload contains a ``score`` field use that value directly (rail-1
# credit-share will read this in SIP-P2).
_REWARD_SIGN: dict[str, float] = {
    REWARD_EVAL_OUTCOME: +1.0,
    REWARD_OPERATOR_APPROVAL: +1.0,
    REWARD_TASK_CANCEL: -1.0,
    REWARD_CAT_C_DENIAL: -1.0,
    REWARD_DRIFT_OVERAGE: -1.0,
}


def is_enabled() -> bool:
    """True iff the policy layer is opt-in-enabled for this process.

    Default ``False`` — Phase 1 ships the seam in the *disabled* state
    so the gatekeeper landing is a pure no-op for current operators.
    Set ``ACC_POLICY_LAYER_ENABLED=true`` to subscribe the harness to
    the bus.  SIP-P1 will flip the default once the subscription path
    is fully covered.
    """
    raw = os.environ.get("ACC_POLICY_LAYER_ENABLED", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


class RewardHarness:
    """Subscribes to reward-bearing subjects and logs each event.

    Phase 1 emits one JSON line per reward to ``acc.policy_layer``;
    SIP-P1 will add an EWMA aggregator and per-role tunable θ store on
    top of these exact subscriptions.

    The harness takes a :class:`SignalingBackend` (NATS) at construction
    time but **does not subscribe in ``__init__``** — callers explicitly
    invoke :meth:`subscribe_all` so test harnesses can wire fakes
    without an event loop.  When :func:`is_enabled` returns False,
    :meth:`subscribe_all` is a no-op and no bus traffic flows.
    """

    def __init__(
        self,
        signaling,
        collective_id: str,
        *,
        role: str = "",
        ewma_alpha: float = _DEFAULT_EWMA_ALPHA,
    ) -> None:
        self._signaling = signaling
        self._cid = collective_id
        self._role = role
        self._subscribed = False
        # Per-role policy vector — defaults to DEFAULT_POLICY_VECTOR.
        # Phase 1 keeps this read-only (no updates).  Operator can pin
        # values via the role.yaml ``policy_pinned`` field (SIP-P2).
        self.theta: dict[str, float] = dict(DEFAULT_POLICY_VECTOR)
        # SIP-P1 — per-reward-kind EWMA aggregator.  Initialised lazily
        # on the first observation per kind so a "no signal yet" caller
        # gets None rather than a misleading 0.0.  Update rule:
        #     ewma = α * x + (1 - α) * ewma_prev
        self._ewma_alpha = max(0.0, min(1.0, float(ewma_alpha)))
        self._ewma: dict[str, float] = {}
        self._reward_counts: dict[str, int] = {}

    @property
    def subscribed(self) -> bool:
        return self._subscribed

    async def subscribe_all(self) -> None:
        """Subscribe to the four reward subjects (when enabled)."""
        if not is_enabled():
            logger.debug(
                "policy_layer: ACC_POLICY_LAYER_ENABLED off — skipping subscribe"
            )
            return
        # Import inside the method so the module stays importable even
        # when acc.signals isn't loadable (test isolation).
        from acc.signals import (  # noqa: PLC0415
            subject_eval_outcome_all,
            subject_oversight_decision_all,
            subject_alert,
        )

        # Bind a thin closure per subject so the logged event carries
        # the right ``kind`` without runtime branching.  Drift updates
        # ride the heartbeat payload (not a dedicated subject); SIP-P1
        # will add a thin drift extractor reading the same stream.
        async def _on_eval(msg: object) -> None:
            self._record(REWARD_EVAL_OUTCOME, msg)

        async def _on_oversight(msg: object) -> None:
            self._record(REWARD_OPERATOR_APPROVAL, msg)

        async def _on_alert(msg: object) -> None:
            self._record(REWARD_CAT_C_DENIAL, msg)

        try:
            await self._signaling.subscribe(
                subject_eval_outcome_all(self._cid), _on_eval,
            )
            await self._signaling.subscribe(
                subject_oversight_decision_all(self._cid), _on_oversight,
            )
            await self._signaling.subscribe(
                subject_alert(self._cid), _on_alert,
            )
            self._subscribed = True
            logger.info(
                "policy_layer: subscribed reward harness for role=%r cid=%s",
                self._role, self._cid,
            )
        except Exception:
            # Non-fatal — a partial subscribe still feeds the harness
            # with whatever subjects DID land.  Log + carry on.
            logger.exception(
                "policy_layer: failed to subscribe one or more reward subjects"
            )

    def _record(self, kind: str, msg: object) -> None:
        """Log one reward observation + update the per-kind EWMA.

        AoA-P1 shipped this as log-only.  SIP-P1 (this revision) adds
        the EWMA aggregator on top — still NO policy update.  SIP-P2
        will read the aggregator and run the bandit update under the
        six rails (frozen-in-AUTO, drift-as-constraint, rate-limit
        vs Cat-C, etc.).

        Numeric reward score:
        - ``eval_outcome`` payloads with a ``score`` field use that
          value directly so SIP-P2 can do credit-share (rail 1).
        - Otherwise the per-kind sign convention in ``_REWARD_SIGN``
          applies (+1 / -1).
        - Unknown kinds default to 0.0 (silently observable, doesn't
          move the EWMA).
        """
        try:
            payload = json.loads(_payload_bytes(msg))
        except Exception:
            payload = {}
        score = self._extract_score(kind, payload)
        # EWMA update — first observation initialises with the value
        # itself (no prior to weight against) so we don't anchor on 0.
        prev = self._ewma.get(kind)
        if prev is None:
            self._ewma[kind] = score
        else:
            self._ewma[kind] = (
                self._ewma_alpha * score
                + (1.0 - self._ewma_alpha) * prev
            )
        self._reward_counts[kind] = self._reward_counts.get(kind, 0) + 1
        record = {
            "ts": time.time(),
            "kind": kind,
            "role": self._role,
            "cid": self._cid,
            "score": score,
            "ewma": self._ewma[kind],
            "count": self._reward_counts[kind],
            "payload": payload,
        }
        try:
            logger.info("reward %s", json.dumps(record, default=str))
        except Exception:
            logger.exception("policy_layer: failed to log reward record")

    @staticmethod
    def _extract_score(kind: str, payload: dict) -> float:
        """Numeric score for one reward observation.

        Eval-outcome payloads carry an explicit ``score`` field (often
        in [0, 1]); we use it verbatim so SIP-P2's credit-share has a
        reliable scalar.  Other reward kinds fall back to the
        per-kind sign in ``_REWARD_SIGN``.
        """
        if kind == REWARD_EVAL_OUTCOME and isinstance(payload, dict):
            score = payload.get("score")
            if isinstance(score, (int, float)):
                return float(score)
        return float(_REWARD_SIGN.get(kind, 0.0))

    def ewma(self, kind: str) -> float | None:
        """Current per-kind EWMA, or ``None`` when no observation yet."""
        return self._ewma.get(kind)

    def reward_count(self, kind: str) -> int:
        """Number of observations recorded for ``kind``."""
        return self._reward_counts.get(kind, 0)

    def snapshot(self) -> dict[str, Any]:
        """Render the harness's current state.

        Used by the (future) Diagnostics Policy tab in the TUI + tests.
        Returns a dict carrying θ, per-kind EWMA values, observation
        counts, and the harness's collective / role identity.  SIP-P2
        will use the same shape on the bus as the ``POLICY_UPDATE``
        event payload.
        """
        return {
            "collective_id": self._cid,
            "role": self._role,
            "theta": dict(self.theta),
            "ewma": dict(self._ewma),
            "counts": dict(self._reward_counts),
            "alpha": self._ewma_alpha,
        }


def _payload_bytes(msg: object) -> bytes:
    """Tolerate both NATS ``Msg`` objects and raw ``bytes`` in tests."""
    if isinstance(msg, (bytes, bytearray)):
        return bytes(msg)
    return bytes(getattr(msg, "data", b""))


__all__ = [
    "DEFAULT_POLICY_VECTOR",
    "REWARD_EVAL_OUTCOME",
    "REWARD_OPERATOR_APPROVAL",
    "REWARD_TASK_CANCEL",
    "REWARD_CAT_C_DENIAL",
    "REWARD_DRIFT_OVERAGE",
    "RewardHarness",
    "is_enabled",
]
