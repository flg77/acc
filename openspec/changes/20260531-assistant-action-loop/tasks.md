# Tasks — `20260531-assistant-action-loop`

## Phase 1 — perception snapshot + system-prompt injection

- [ ] `acc/perception.py` — new module:
  - `PerceptionSnapshot` Pydantic model (capabilities, roster,
    sub_collectives, snapshot_ts, capability_revision, stale).
  - `snapshot_for_assistant(bus, cid, operating_mode)` async
    function — parallel fan-out to capability_query + roster_snapshot;
    100ms hard timeout; TTL-cached.
  - Fallback path: local `roles/` scan when orchestrator times out.
- [ ] `acc/signals.py` — `subject_roster_snapshot(cid)` →
      `acc.{cid}.roster.snapshot`.
- [ ] `acc/agent.py` (arbiter path) — handler on
      `subject_roster_snapshot` that replies with the in-memory
      registration table grouped by role.
- [ ] `acc/cognitive_core.py` — perception call BEFORE
      `build_system_prompt` when role == assistant; `build_system_prompt`
      gains a `## Currently available` fenced block.
- [ ] Token-budget cap (`ACC_PERCEPTION_PROMPT_TOKENS`, default
      600); embedding-similarity filtering against the operator
      prompt.
- [ ] Marker dispatch validation: `target_role` in
      `[PROPOSE_SPAWN:role:...]` MUST be in snapshot roster OR
      capability list; reject + log if hallucinated.
- [ ] `roles/assistant/role.yaml` bump to **v2.2.0**: seed_context
      references the `## Currently available` block ("ALWAYS check
      the Currently available block before recommending any role
      or MCP").
- [ ] Tests:
  - `tests/test_perception_snapshot.py` — unit: capability +
    roster + sub-collectives mock; TTL cache; fallback path; token
    budget cap.
  - `tests/test_assistant_phase1_perception.py` — end-to-end:
    snapshot injected into system prompt; marker validation
    rejects hallucinated roles.
  - `tests/test_arbiter_roster_snapshot.py` — arbiter replies
    with current registrations.

## Phase 2 — hybrid dual-format marker emission — DEFERRED

Gates on Phase 1 landed + at least one live trace showing reduced
hallucination.

- [ ] Seed_context updated: model emits both `<reasoning>` block
      AND `<action>{...JSON...}</action>` block.
- [ ] `acc/assistant_proposal.py::parse_proposal_markers` handles
      both legacy brackets and new JSON action blocks.
- [ ] Tests: small model fixture (mocked) emits JSON action; parser
      extracts correctly.

## Phase 3 — AUTO contract enforcement — DEFERRED

- [ ] Reasoning-action mismatch detector: when mode == AUTO and
      reasoning contains action language ("I will spawn / route /
      delegate") but no marker is emitted, run extractor.
- [ ] Extractor: cheap second LLM call ("emit the action marker
      now") OR rule-based extraction from reasoning text.
- [ ] Oversight escalation when extraction also fails: queue a
      "no marker emitted" item with the reasoning text + operator
      prompt.

## Phase 4 — push-based roster snapshot — DEFERRED

- [ ] Arbiter publishes periodic `ROSTER_SNAPSHOT` (every 30s) on
      `subject_roster_snapshot(cid)` (same subject; broadcast not
      RPC).
- [ ] `PerceptionSnapshot` reads from a local cache; falls back to
      RPC if cache > 60s stale.

## Phase 5 — Reflexion-style reconciliation + SIP reward — DEFERRED

- [ ] Compare reasoning vs. dispatched markers per task.
- [ ] Emit a `REASONING_ACTION_DELTA` event (new bus subject).
- [ ] SIP-P2 harness consumes this as a reward signal (new
      composite-reward kind).

## Follow-on proposals to spawn after Phase 1 lands

- [ ] `20260601-assistant-action-loop-phase-2`
- [ ] `20260601-assistant-action-loop-phase-3`
- [ ] `20260601-assistant-action-loop-phase-4`
- [ ] `20260601-assistant-action-loop-phase-5`
