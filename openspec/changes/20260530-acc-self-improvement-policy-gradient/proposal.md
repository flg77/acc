# OpenSpec — Per-role policy-gradient self-improvement

| Field | Value |
|---|---|
| Change ID | `20260530-acc-self-improvement-policy-gradient` |
| Status | Phases 1 + 2 + 3 LANDED (v0.3.25, v0.3.28, v0.3.32); Phase 4 carved into sibling `20260531-acc-self-improvement-marl-credit-assignment` |
| Companion | `20260530-role-proposal-assistant-agent-of-agents` |
| Sequencing contract | `Notes/.../20260530-aoa-and-self-improve — integration & sequencing.md` |
| Notes mirror | `Notes/Development/AgenticCellCorpus/ACC Openspec/20260530-acc-self-improvement-policy-gradient — OpenSpec (proposed).md` |

## Problem statement

ACC has rich learning surfaces — memory notes (PR-MEM1/2/3), drift
scores (ACC-6a/11), Cat-C rule proposals (PR-Z3), self-challenge
(PR-Z3e), operator queue verdicts, EVAL_OUTCOME reviews — but only
two of them closed a behavioural loop pre-SIP: memory notes
(episodic, in-context) and Cat-C proposals (governance changes via
human queue).

The other four signals (operator approvals, EVAL_OUTCOME scores,
drift deltas, TASK_CANCEL rates) were observed but not fed back to
the cognitive core's decision policy. The operator complaint that
triggered this proposal was *"I haven't spotted proof for real
autonomy capabilities."* SIP closes those loops.

## Phase summary (Phases 1–3 LANDED; Phase 4 carved out)

| Phase | Version | Output |
|---|---|---|
| 1 | v0.3.25 | `acc/policy_layer.py` reward harness. Subscribes to EVAL_OUTCOME / oversight verdicts / Cat-A alert. EWMA aggregator. `RewardHarness.subscribe_all` / `_record` / `snapshot`. Opt-in via `ACC_POLICY_LAYER_ENABLED`. |
| 2 | v0.3.28 | Composite reward + drift constraint + frozen-in-AUTO + windowed θ update. Six rails enforced. `subject_policy_update(cid, role)`. POLICY_UPDATE event published; OTel pipeline_tracing promotes to MLflow span event. Tests: 30 in `test_sip_phase2_bandit.py`. |
| 3 | v0.3.32 | `ContextFeatures` dataclass; `RewardHarness.set_context`/`contextual_bias`/`update_context_weights`; `policy_contextual` flag on RoleDefinitionConfig. Default OFF preserves SIP-P2 byte-identically. Tests: 20 in `test_sip_phase3_contextual.py`. |
| 4 | — | Multi-agent credit assignment. Carved out as sibling proposal `20260531-acc-self-improvement-marl-credit-assignment`. |

## Six rails (carried through every phase)

1. **Reward-hacking via routing** — credit-share to the delegator
   capped at 0.5 (Phase 4); rail-1 stays cheap heuristic until Phase
   4 lands.
2. **Drift-as-constraint, not reward** — drift below `drift_cap`
   contributes zero to the composite; drift above adds a negative
   penalty proportional to overage.
3. **Policy ↔ Cat-C race** — bandit cadence (`policy_update_every_
   n_tasks`) is slower than Cat-C proposal cadence so governance has
   time to catch up.
4. **Memory ↔ policy disagreement** — memory notes are conditional
   priors; policy is unconditional default; operating mode breaks ties.
5. **Multi-agent credit** — Phase 1–3 ship single-agent rewards only.
   Multi-agent credit is sibling proposal.
6. **Frozen-in-AUTO** — `observe_task` is a no-op under
   `MODE_AUTO`; promoting to AUTO trades learning for stability.

## Linked

- Companion: `20260530-role-proposal-assistant-agent-of-agents` (Phase 6 wires the
  Assistant first).
- Sibling (multi-agent credit): `20260531-acc-self-improvement-marl-
  credit-assignment`.
- Sequencing contract: integration & sequencing note.
- Telemetry: `20260527-mlflow-otel-telemetry` Phase 4 promotes
  POLICY_UPDATE to MLflow span events automatically.
