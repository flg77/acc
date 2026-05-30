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
from typing import Any, Optional

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


# SIP-P2 — composite-reward weights.  Tuned so positive eval/approval
# move θ but a single Cat-C denial spike dominates (rail 3 — policy
# backs off *before* the compliance officer needs to propose a rule).
_COMPOSITE_WEIGHTS: dict[str, float] = {
    REWARD_EVAL_OUTCOME: 1.0,
    REWARD_OPERATOR_APPROVAL: 1.5,
    REWARD_TASK_CANCEL: 1.0,
    REWARD_CAT_C_DENIAL: 2.0,
}


# SIP-P2 — per-knob bounds.  θ updates clamp to these so a runaway
# composite signal can't drive a threshold below 0 or above 1.
# memory_top_k and reasoning_depth_target sit on integers, so their
# ranges are wider.  Values pinned to mirror operator-tunable expectations.
_DEFAULT_THETA_BOUNDS: dict[str, tuple[float, float]] = {
    "route_confidence_threshold": (0.0, 1.0),
    "spawn_threshold": (0.0, 1.0),
    "delegate_domain_match": (0.0, 1.0),
    "memory_top_k": (1.0, 20.0),
    "reasoning_depth_target": (1.0, 12.0),
}


# SIP-P2 — per-knob hill-climbing step size.  Scaled to each knob's
# range so the update cadence feels comparable across knobs.
_DEFAULT_THETA_STEP: dict[str, float] = {
    "route_confidence_threshold": 0.02,
    "spawn_threshold": 0.02,
    "delegate_domain_match": 0.02,
    "memory_top_k": 0.5,
    "reasoning_depth_target": 0.5,
}


# SIP-P2 — rail 2 (drift-as-constraint).  Drift contributes to the
# composite reward ONLY above this cap; below the cap it is zero so
# the agent isn't punished for legitimate exploration toward the
# role centroid.  Operators can override per-deployment via the env.
_DEFAULT_DRIFT_CAP = 0.8


# SIP-P2 — task-window for policy updates.  Rail 3 (rate limit vs
# Cat-C): one update per N completed tasks is intentionally slow so
# the compliance officer's Cat-C rule proposals get time to land
# before policy drifts in a direction the rules then forbid.
_DEFAULT_UPDATE_EVERY = 100


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
        update_every: int = _DEFAULT_UPDATE_EVERY,
        pinned: Optional[frozenset[str]] = None,
        bounds: Optional[dict[str, tuple[float, float]]] = None,
        drift_cap: float = _DEFAULT_DRIFT_CAP,
    ) -> None:
        self._signaling = signaling
        self._cid = collective_id
        self._role = role
        self._subscribed = False
        # Per-role policy vector — defaults to DEFAULT_POLICY_VECTOR.
        # Phase 1 ships read-only.  SIP-P2 (this revision) drives this
        # vector via the hill-climbing update below.
        self.theta: dict[str, float] = dict(DEFAULT_POLICY_VECTOR)
        # SIP-P1 — per-reward-kind EWMA aggregator.  Initialised lazily
        # on the first observation per kind so a "no signal yet" caller
        # gets None rather than a misleading 0.0.  Update rule:
        #     ewma = α * x + (1 - α) * ewma_prev
        self._ewma_alpha = max(0.0, min(1.0, float(ewma_alpha)))
        self._ewma: dict[str, float] = {}
        self._reward_counts: dict[str, int] = {}
        # SIP-P2 — bandit configuration.
        # Cadence (rail 3 — rate-limit vs Cat-C); clamp to >=1 so a bad
        # config can't trigger an update on every task.
        self._update_every = max(1, int(update_every))
        # Pinned θ knobs — never moved by the bandit.
        # Operators set this via role.yaml ``policy_pinned``; defaults
        # to all knobs pinned during bootstrap (operator opts in).
        if pinned is None:
            self._pinned: frozenset[str] = frozenset(DEFAULT_POLICY_VECTOR.keys())
        else:
            self._pinned = frozenset(pinned)
        # Per-knob bounds for clamping after a step.
        self._bounds = dict(_DEFAULT_THETA_BOUNDS)
        if bounds:
            self._bounds.update(bounds)
        # Drift constraint (rail 2).
        self._drift_cap = max(0.0, min(1.0, float(drift_cap)))
        # Hill-climbing momentum per knob (±1 direction).  Initialised
        # to +1 so the first step probes the upper half of the range.
        self._momentum: dict[str, int] = {k: 1 for k in DEFAULT_POLICY_VECTOR}
        # Last composite reward seen — used to decide whether to keep
        # or flip the per-knob direction.  None on cold start.
        self._last_composite: Optional[float] = None
        # Task counter (rail 3 cadence).
        self._tasks_in_window = 0
        # Update history — ring of (ts, old_theta, new_theta,
        # composite_reward).  Bounded so a long-running agent doesn't
        # grow this unboundedly.
        self._update_history: list[dict[str, Any]] = []
        # Most-recent task drift score — fed in via observe_task() so
        # the composite reward can apply the constraint penalty.
        self._last_task_drift: float = 0.0

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
        counts, the harness's collective / role identity, and the
        SIP-P2 bandit fields (window state + history).
        """
        return {
            "collective_id": self._cid,
            "role": self._role,
            "theta": dict(self.theta),
            "ewma": dict(self._ewma),
            "counts": dict(self._reward_counts),
            "alpha": self._ewma_alpha,
            # SIP-P2 fields.
            "pinned": sorted(self._pinned),
            "drift_cap": self._drift_cap,
            "update_every": self._update_every,
            "tasks_in_window": self._tasks_in_window,
            "last_composite": self._last_composite,
            "update_history": list(self._update_history),
        }

    # ------------------------------------------------------------------
    # SIP-P2 — composite reward + windowed bandit update
    # ------------------------------------------------------------------

    def composite_reward(self, drift: Optional[float] = None) -> float:
        """Return the weighted scalar that drives the bandit step.

        Rail 2 (drift-as-constraint): drift contributes only when it
        exceeds ``drift_cap``; below the cap the term is zero so the
        agent isn't punished for legitimate centroid exploration.

        ``drift`` defaults to the last value observed via
        :meth:`observe_task`.  Negative-signed rewards
        (cat_c_denial, task_cancel, drift_overage) subtract; positive
        ones (eval_outcome, operator_approval) add.  Missing EWMAs
        treated as 0 — first-observation anchoring (SIP-P1) means a
        kind seen at least once already has a non-zero value.
        """
        d = float(drift) if drift is not None else self._last_task_drift
        # Eval + approval lift the composite; cancel + cat-c subtract.
        r = 0.0
        r += _COMPOSITE_WEIGHTS[REWARD_EVAL_OUTCOME] * float(
            self._ewma.get(REWARD_EVAL_OUTCOME, 0.0)
        )
        r += _COMPOSITE_WEIGHTS[REWARD_OPERATOR_APPROVAL] * float(
            self._ewma.get(REWARD_OPERATOR_APPROVAL, 0.0)
        )
        r -= _COMPOSITE_WEIGHTS[REWARD_TASK_CANCEL] * abs(float(
            self._ewma.get(REWARD_TASK_CANCEL, 0.0)
        ))
        r -= _COMPOSITE_WEIGHTS[REWARD_CAT_C_DENIAL] * abs(float(
            self._ewma.get(REWARD_CAT_C_DENIAL, 0.0)
        ))
        # Drift-as-constraint (rail 2).  Penalty is the overage
        # magnitude scaled by the cat-c weight (drift overages are
        # treated as a serious signal — they often precede Cat-C trips).
        overage = max(0.0, d - self._drift_cap)
        if overage > 0:
            r -= _COMPOSITE_WEIGHTS[REWARD_CAT_C_DENIAL] * overage
        return r

    async def observe_task(
        self,
        operating_mode: str,
        drift: Optional[float] = None,
    ) -> bool:
        """Record one completed task and run a bandit update if due.

        Rail 6 (frozen-in-AUTO): when ``operating_mode == "AUTO"``,
        this is a no-op — the policy does NOT update while no human
        is in the loop.  Operators promoting to AUTO get a stable
        behaviour snapshot; SIP-P3+ can revisit if telemetry justifies.

        Rail 3 (rate limit vs Cat-C): the in-window task counter
        increments per call; ``_maybe_update_theta`` fires every
        ``update_every`` tasks.  Cat-C proposals (PR-Z3) operate on a
        much faster cadence, so by the time the policy moves, the
        rule landscape has settled.

        Returns True iff a θ update fired on this call (useful for
        tests + the TUI's "next update in N tasks" surface).
        """
        from acc.operating_modes import MODE_AUTO, normalise  # noqa: PLC0415
        if normalise(operating_mode or "") == MODE_AUTO:
            return False
        if drift is not None:
            self._last_task_drift = float(drift)
        self._tasks_in_window += 1
        if self._tasks_in_window < self._update_every:
            return False
        try:
            await self._update_theta()
        finally:
            self._tasks_in_window = 0
        return True

    async def _update_theta(self) -> None:
        """One hill-climbing step + audit event.

        Per unpinned knob: if the composite reward improved since the
        last update, take a step in the current direction; otherwise
        flip the direction and step.  Clamp to bounds.  Publish on
        ``subject_policy_update``.
        """
        old_theta = dict(self.theta)
        composite = self.composite_reward()
        moved: dict[str, float] = {}
        if self._last_composite is None:
            # Cold start — just anchor; no movement yet.
            self._last_composite = composite
        else:
            improved = composite >= self._last_composite
            for knob in DEFAULT_POLICY_VECTOR:
                if knob in self._pinned:
                    continue
                if not improved:
                    self._momentum[knob] *= -1
                step = _DEFAULT_THETA_STEP.get(knob, 0.01)
                proposed = self.theta[knob] + (step * self._momentum[knob])
                lo, hi = self._bounds.get(knob, (0.0, 1.0))
                clamped = max(lo, min(hi, proposed))
                if clamped != self.theta[knob]:
                    moved[knob] = clamped - self.theta[knob]
                self.theta[knob] = clamped
            self._last_composite = composite

        # Audit + bus event — always emitted (even when no knob moved)
        # so the operator sees that an update window completed.
        event = {
            "ts": time.time(),
            "old": old_theta,
            "new": dict(self.theta),
            "moved": moved,
            "composite_reward": composite,
            "tasks_in_window": self._update_every,
        }
        self._update_history.append(event)
        # Bound the in-memory history so a long-running harness
        # doesn't grow unboundedly.  Last 64 updates is enough for
        # the TUI's recent-history view; older entries hit the bus
        # audit + (future) MLflow span event from the OTel proposal.
        if len(self._update_history) > 64:
            del self._update_history[: len(self._update_history) - 64]
        try:
            from acc.signals import subject_policy_update  # noqa: PLC0415
            payload = {
                "collective_id": self._cid,
                "role": self._role,
                "theta": dict(self.theta),
                "update": event,
            }
            await self._signaling.publish(
                subject_policy_update(self._cid, self._role), payload,
            )
        except Exception:
            logger.exception(
                "policy_layer: POLICY_UPDATE publish failed for role=%s",
                self._role,
            )

    def reset_theta(self) -> None:
        """Revert θ to ``DEFAULT_POLICY_VECTOR`` (operator escape hatch).

        Clears the momentum vector + last-composite anchor so the next
        update window starts from a clean state.  Update history is
        retained so the audit trail shows the reset.
        """
        self.theta = dict(DEFAULT_POLICY_VECTOR)
        self._momentum = {k: 1 for k in DEFAULT_POLICY_VECTOR}
        self._last_composite = None
        self._tasks_in_window = 0
        # Audit trail: a reset entry.
        self._update_history.append({
            "ts": time.time(),
            "old": "(prior θ)",
            "new": dict(self.theta),
            "moved": {},
            "composite_reward": 0.0,
            "tasks_in_window": 0,
            "reason": "operator_reset",
        })


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
