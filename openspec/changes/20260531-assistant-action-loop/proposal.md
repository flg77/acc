# OpenSpec — Assistant action loop (perceive → decide → act)

| Field | Value |
|---|---|
| Change ID | `20260531-assistant-action-loop` |
| Status | Proposed (Phase 1 ready to implement) |
| Companion | `20260530-assistant-agent-of-agents` (the gatekeeper this hardens) |
| Builds on | v0.3.42 orchestrator CapabilityIndex (PR #8) — the consumer side that was missing |
| Notes mirror | `Notes/.../ACC Openspec/20260531-assistant-action-loop — OpenSpec (proposed).md` |
| Brainstorm | `Notes/.../ACC-Assistant-Action-Loop/Assistant action loop — brainstorm.md` |

## Problem statement

The Assistant gatekeeper (proposal `20260530-assistant-agent-of-agents`)
shipped its full eight-phase ledger over 2026-05-30 — default
target, dormant-watcher, proposal markers, queue-gated dispatcher,
sub-collective registry, AgentCard, operator identity, role policy
learning. Today on lighthouse (small llama-3.2-3B-FP8 model,
v0.3.41 stack, mode AUTO) a four-turn conversation revealed three
gaps the AoA proposal didn't address:

1. **The Assistant hallucinates capabilities.** It told the
   operator "use the `worker-pool` role and the `prompt` role" —
   neither exists in `roles/`. The seed_context describes ACC
   concepts in the abstract but doesn't list **what's actually
   available** in this collective right now.
2. **It doesn't know what's already running.** A `coding_agent` is
   live in the baseline roster (since v0.3.35 PR #1), but the
   Assistant has no idea — it treats every conversation as cold-
   start.
3. **PROPOSE markers don't fire on small models.** The reasoning
   block clearly says "I will execute Option A, spawning the
   agentset"; the reply text says "I will guide the operator
   through the process". The marker parser finds nothing to
   dispatch. The AoA-P2b queue stays empty. Under AUTO mode the
   dispatcher has nothing to act on.

All three problems share a single architectural cause: the
cognitive_core has no **Observe step**. It builds the system
prompt from a static `seed_context`, calls the LLM, parses
markers. Nothing in between snapshots the live state of the
collective. Everything needed to fix this is already in the
codebase (CapabilityIndex via PR #8; heartbeat ring on every
agent; AoA-P3a sub-collective registry; mode-aware dispatcher) —
but no wiring connects them to the LLM call.

This proposal adds the wiring.

## Design decisions (bootstrap defaults from the brainstorm's 7 questions)

1. **Marker-reliability approach** — Phase 2 ships **hybrid**:
   the model emits both `<reasoning>...</reasoning>` AND
   `<action>{...JSON...}</action>`. Parser handles both legacy
   bracket markers and new JSON. Small models opt into JSON;
   large models can use either.
2. **Snapshot scope** — same-collective only in Phase 1;
   sub-collectives via AoA-P3a registry in Phase 4.
3. **Snapshot freshness** — per-task NATS request/reply in
   Phase 1; push-based cache (arbiter publishes periodic
   `ROSTER_SNAPSHOT`) in Phase 4.
4. **AUTO failure mode** — extraction (cheap second LLM call)
   → fall back to oversight escalation (operator sees reasoning
   + "no marker emitted" note).
5. **Where perception lives** — separate **`acc/perception.py`**
   pipeline step; cognitive_core calls into it. Looser coupling +
   more testable than inlining.
6. **Dreamer interaction** — perception snapshot is independent of
   M11; can be reused later as input to M11 in dreamer Phase 5.
7. **Live-state scope** — Phase 1 covers capability + roster +
   sub-collectives. Recent-prompt history (last N tasks) is a
   Phase 3 extension.

## Three invariants (table stakes — every phase)

1. **Stale-OK > stale-block.** Perception snapshot has a TTL
   cache; if the orchestrator capability_query times out, the
   prompt block notes "[stale > 60s]" and the Assistant proceeds
   with cached data. Never block task processing > 100ms on
   perception.
2. **Backward compatibility on markers.** Legacy bracket markers
   (`[PROPOSE_SPAWN:role:cluster:reason]`) keep working. JSON
   action blocks add a parallel path; the parser tries both.
3. **AUTO must act.** When mode == AUTO and the reasoning trace
   contains action-shaped language, the cognitive_core MUST
   either dispatch a marker (extracted or emitted) or escalate
   to the operator. Silent "explanation in AUTO" is a
   contract violation — Phase 3 closes this.

## Phase summary

| Phase | Status | Mechanism | Operator-visible delta |
|---|---|---|---|
| 1 | Proposed | Perception snapshot (capability + roster + sub-collectives) → system-prompt injection | Hallucinated role names go away; assistant references live roster |
| 2 | Deferred | Hybrid dual-format reasoning + JSON action emission | Marker emission becomes reliable on 3B-class models |
| 3 | Deferred | AUTO contract enforcement: extractor → oversight escalation | AUTO actually acts, even on small models |
| 4 | Deferred | Push-based roster snapshot (arbiter → cache) | Perception step latency drops to ~0 |
| 5 | Deferred | Reflexion-style reasoning-vs-action reconciliation; SIP reward signal | Bandit learns when reasoning aligns with action |

## Phase 1 design (the perception snapshot)

### `acc/perception.py` (new)

```python
class PerceptionSnapshot(BaseModel):
    """What the Assistant sees before composing its system prompt.

    Built per-task; TTL-cached for ``ACC_PERCEPTION_TTL_S`` (default 30s).
    """
    capabilities: list[CapabilityMatch]  # from orchestrator
    roster: dict[str, list[str]]         # role -> [agent_id, ...]
    sub_collectives: dict[str, dict]     # from CollectiveSpec
    snapshot_ts: float
    capability_revision: int             # from CapabilityReply
    stale: bool                          # True if any source > TTL

async def snapshot_for_assistant(
    bus: SignalingBackend,
    cid: str,
    operating_mode: OperatingMode,
) -> PerceptionSnapshot: ...
```

### `acc/cognitive_core.py` integration

Just before `build_system_prompt` (when `role == "assistant"`):

```python
if self._role == "assistant":
    perception = await snapshot_for_assistant(
        bus=self._bus,
        cid=self._cid,
        operating_mode=self._operating_mode,
    )
    self._perception = perception  # available to build_system_prompt
```

`build_system_prompt` gains a `## Currently available` fenced block:

```
## Currently available in collective {cid}

Running agents (by role):
  assistant: assistant-1
  coding_agent: coding-1
  compliance_officer: compliance-1
  arbiter: arbiter-a19ddbe2
  analyst: analyst-1
  ingester: ingester-1

Available roles (not yet running):
  research_planner (purpose: ...)
  clinical_reviewer (purpose: ...)
  ...

Available MCPs:
  echo_server (LOW): diagnostic JSON-RPC echo
  web_search_brave (MEDIUM): ...

Managed sub-collectives:
  sol-code: domain=software_engineering, status=...
  sol-medical: domain=clinical_research, status=...
```

### Cap on token budget

The block is capped at `ACC_PERCEPTION_PROMPT_TOKENS` (default 600
tokens). Filtering (in order until budget met):

1. Always-include: running agents (always small).
2. Embedding-similarity to operator prompt: include top-K matching
   roles + MCPs.
3. Sub-collectives: include all (typically ≤ 5).

### Phase 1 deliverables

- `acc/perception.py` — `PerceptionSnapshot` model + `snapshot_for_assistant`.
- `acc/cognitive_core.py` — perception call + system-prompt block
  injection (assistant role only).
- `acc/signals.py` — `subject_roster_snapshot(cid)` request/reply.
- `acc/agent.py` (arbiter path) — reply handler on
  `subject_roster_snapshot` returning the in-memory registration
  table.
- `roles/assistant/role.yaml` (bump to v2.2.0) — seed_context
  references the `## Currently available` block.
- `tests/test_perception_snapshot.py` — unit + integration tests
  (mock CapabilityIndex + mock arbiter roster reply).
- `tests/test_assistant_phase1_perception.py` — end-to-end:
  capability + roster snapshot injected into system prompt.

### Phase 1 does NOT include

- JSON action blocks (Phase 2).
- AUTO enforcement on missing markers (Phase 3).
- Push-based roster snapshot (Phase 4 — Phase 1 uses RPC).
- Reasoning-vs-action reconciliation (Phase 5).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Capability query times out → assistant proceeds with no live data | TTL cache; "[stale]" annotation in prompt; fall back to local role scan |
| Perception adds latency on hot path | 100ms hard budget; parallel fan-out to capability + roster |
| System prompt blows past token budget | `ACC_PERCEPTION_PROMPT_TOKENS` cap; embedding-similarity filtering |
| Orchestrator not deployed → no capability source | Phase 1 includes a fallback: local `roles/` scan inside perception module |
| Marker hallucination by the LLM (`[PROPOSE_SPAWN:fake_role:...]`) | Validate `target_role` against snapshot roster + capability before dispatch; reject + log |
| Cache staleness racing role updates | TTL invalidation on `subject_role_assign` + `subject_role_update` events |
| Push-based snapshot (Phase 4) bus storm | Phase 4-only concern; throttle at ≤ 1/30s |

## Follow-on proposals (spawn after Phase 1 lands)

- `20260601-assistant-action-loop-phase-2` — dual-format marker
  emission (reasoning + JSON action).
- `20260601-assistant-action-loop-phase-3` — AUTO contract
  enforcement (extractor → escalation).
- `20260601-assistant-action-loop-phase-4` — push-based roster
  snapshot.
- `20260601-assistant-action-loop-phase-5` — Reflexion-style
  reconciliation + SIP reward signal.

## Linked

- Companion: `20260530-assistant-agent-of-agents` — the gatekeeper.
- Builds on: v0.3.42 PR #8 — orchestrator CapabilityIndex
  (the source the perception snapshot reads).
- Brainstorm: `Notes/.../ACC-Assistant-Action-Loop/Assistant action loop — brainstorm.md`
- Live trigger: 2026-05-31 18:51–18:56 lighthouse trace (4 screenshots
  in chat transcript).
