# Tasks — `20260530-acc-self-improvement-policy-gradient`

## Phase 1 — reward harness + EWMA aggregation (LANDED v0.3.25)

- [x] `acc/policy_layer.py` module with `RewardHarness`,
      `DEFAULT_POLICY_VECTOR`, sign convention.
- [x] Subscribe to `subject_eval_outcome_all`,
      `subject_oversight_decision_all`, `subject_alert`.
- [x] Per-reward-kind EWMA aggregator (α=0.30; first-observation
      anchoring; α-clamping to [0,1]).
- [x] `_record(kind, msg)` logs + updates EWMA.
- [x] `snapshot()` returns θ + ewma + counts + identity.
- [x] Agent boot wires `_maybe_start_reward_harness` (opt-in via
      `ACC_POLICY_LAYER_ENABLED`).
- [x] Tests: 16 in `test_policy_layer_subscriptions.py`.

## Phase 2 — composite reward + windowed bandit + six rails (LANDED v0.3.28)

- [x] `composite_reward(drift)` — weighted sum of EVAL,
      OPERATOR_APPROVAL, CAT_C_DENIAL (negative); drift overage
      penalty when above `drift_cap`.
- [x] `observe_task(operating_mode, drift)` — frozen-in-AUTO (rail
      6); windowed cadence (rail 3) via `_tasks_in_window`.
- [x] `_update_theta`: hill-climbing per unpinned knob; flip
      momentum on non-improvement; clamp to bounds.
- [x] `pinned: frozenset[str]` + `bounds: dict[str, (lo, hi)]` +
      `update_every: int` + `drift_cap: float` kwargs.
- [x] `reset_theta()` — operator-callable restore.
- [x] `subject_policy_update(cid, role)` + POLICY_UPDATE event.
- [x] Audit history bounded at 64 entries.
- [x] Tests: 30 in `test_sip_phase2_bandit.py`.

## Phase 3 — contextual policy seam (LANDED v0.3.32)

- [x] `ContextFeatures` dataclass (operating_mode one-hot; drift;
      last_eval_reward).
- [x] `RewardHarness.__init__`: `contextual: bool = False`,
      `contextual_lr: float = 0.01` (clamped to [0,1]).
- [x] `set_context(features)` — no-op when contextual off.
- [x] `contextual_bias(knob)` — tanh(W · features), bounded in
      [-1, 1] under float arithmetic.
- [x] `update_context_weights(knob, reward)` — accumulates
      `lr * reward * feature` per (knob, feature).
- [x] `snapshot()` surfaces SIP-P3 fields only when contextual on.
- [x] `RoleDefinitionConfig.policy_contextual: bool = False`.
- [x] Agent boot threads `policy_contextual` into the harness.
- [x] Tests: 20 in `test_sip_phase3_contextual.py`.

## Phase 4 — multi-agent credit assignment (CARVED OUT)

Spawned as sibling proposal `20260531-acc-self-improvement-marl-
credit-assignment`. Three layered mechanisms (trace-conditioned
credit graph; per-agent contribution score; counterfactual eval)
land per their own phasing. Gated on ≥ 4 weeks of POLICY_UPDATE
telemetry and AoA Phase 4's full A2A handover.
