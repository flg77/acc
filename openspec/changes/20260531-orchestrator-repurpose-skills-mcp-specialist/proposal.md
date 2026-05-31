# OpenSpec — Orchestrator repurpose: Skills & MCP specialist

| Field | Value |
|---|---|
| Change ID | `20260531-orchestrator-repurpose-skills-mcp-specialist` |
| Status | Proposed (Phase 1 ready to implement) |
| Closes followup | #37 (orchestrator routing superseded by AoA gatekeeper) |
| Companion | `20260530-assistant-agent-of-agents` (owns routing) · `20260531-acc-role-package-format` (Phase 5 marketplace ride) · `20260530-acc-dreaming-agent` (Phase 6 M9 ride) |
| Notes mirror | `Notes/.../ACC Openspec/20260531-orchestrator-repurpose-skills-mcp-specialist — OpenSpec (proposed).md` |
| Brainstorm | `Notes/.../ACC-Orchestrator-Repurpose/Orchestrator as Skills & MCP specialist — brainstorm.md` |

## Problem statement

PR-V6/V6b (v0.3.11) gave the `orchestrator` role a deliberation-and-
routing surface (`can_route: true`, `[ROUTE:role:reason]`). AoA-P2a
(v0.3.26) gave the Assistant gatekeeper the same surface
(`[PROPOSE_ROUTE:target_role:reason]`) **with stronger governance**:
queue-gated by default (ASK_PERMISSIONS), audit-logged, mode-aware,
and operator-facing as the TUI's default target. Two parallel routing
paths now exist; operators don't know which one to drive; the
orchestrator's path bypasses the queue under PLAN/ACCEPT_EDITS modes.

Follow-up #37 sketched three options (retain / deprecate / merge).
This proposal picks a **fourth — repurpose**: orchestrator stops
competing on routing and starts answering "what's available?" — a
question every other agent (especially the Assistant) currently has
to hand-resolve from `acc-config.yaml` + `mcps/*.yaml` +
`roles/*/role.yaml`.

Repurposed, the orchestrator becomes the **Skills & MCP specialist**:
the single source of truth for the collective's capability catalog,
with a query subject the Assistant can call mid-reasoning and a
recommendation surface that proposes infusions into roles when gaps
are detected.

## Design decisions (bootstrap defaults from the brainstorm's open questions)

1. **Deterministic catalog + LLM rationale (hybrid)** — Phase 1
   ships pure deterministic; Phase 2 adds LLM rationale for
   recommendations only.
2. **NATS request/reply on `capability.query`** — sync, < 50ms
   latency budget, canonical NATS shape.
3. **One orchestrator per collective** — matches today's deployment.
   Queue-group replication added when load requires.
4. **SIP opt-in deferred** — Phase 1 `policy_enabled: false`. Phase
   3 flips it on once approval signals exist.
5. **Filesystem + bus hybrid discovery** — boot scan of
   `acc-config.yaml` / `mcps/` / `roles/`; subscribe to
   `subject_role_assign` + `subject_role_update` for runtime
   updates.
6. **Gap-analyser cadence 30 min** — matches PR-Z3e compliance
   officer self-challenge.
7. **`[ROUTE:...]` deprecation timeline** — Phase 4 warns + auto-
   translates; Phase 7 removes after ≥ 2 minor versions.

## Phase summary

| Phase | Status | Deliverable |
|---|---|---|
| 1 | Proposed | Capability catalog + query subject (read-only) |
| 2 | Deferred | `[RECOMMEND_SKILL]` / `[RECOMMEND_MCP]` markers → AoA-P2b queue |
| 3 | Deferred | Periodic gap analyser; flip `can_route: false` |
| 4 | Deferred | Deprecate `[ROUTE:...]` parser (auto-translate to `[PROPOSE_ROUTE]`) |
| 5 | Deferred (gates on `20260531-acc-role-package-format` Phase 4) | Marketplace catalog mirror |
| 6 | Deferred (gates on `20260530-acc-dreaming-agent` Phase 5) | Dreamer M9 distilled-skill ingestion |
| 7 | Deferred (gates on Phase 4 + ≥ 2 minor versions) | Remove deprecated `[ROUTE:...]` parser |

## Three lifted invariants (table stakes — every phase)

1. **Single routing primitive.** Once Phase 4 lands, routing flows
   only through `[PROPOSE_ROUTE]` (Assistant). No agent emits both.
2. **Catalog ≠ deployment.** Orchestrator owns *what's available*;
   arbiter still owns *what's deployed*. They never overlap.
3. **Recommendation is a proposal.** Phase 2+ recommendations always
   land in the AoA-P2b queue. Orchestrator never mutates role.yaml
   directly; the queue dispatcher does, on operator approval.

## Phase 1 design (catalog + query, read-only)

### `acc/capability_index.py` (new)

```python
class CapabilityIndex:
    """Denormalised collective-scoped catalog of skills + MCPs + roles.

    Built at boot from filesystem scan; refreshed on bus events
    (role_assign, role_update) and SIGHUP.  Cached in Redis with a
    24h TTL; primary source-of-truth is in-process.
    """

    def __init__(self, cid: str, redis_url: str, roles_root: str,
                 mcps_root: str, acc_config_path: str): ...

    async def rebuild(self) -> None:
        """Re-scan filesystem; publish CAPABILITY_INDEX_REBUILT signal."""

    async def query(self, q: CapabilityQuery) -> CapabilityReply:
        """Match against the catalog.  Pure deterministic in Phase 1."""

    async def subscribe_invalidation(self, bus: Bus) -> None:
        """Listen for role_assign / role_update; mark cache dirty."""
```

### `acc/signals.py` (extend)

- `subject_capability_query(cid: str) -> str` →
  `acc.{cid}.capability.query`
- `subject_capability_recommend(cid: str) -> str` →
  `acc.{cid}.capability.recommend` (informational mirror; primary
  channel is the AoA-P2b queue)

### `acc/agent.py` (extend)

- Boot hook: when `self.config.agent.role == "orchestrator"`,
  instantiate `CapabilityIndex` and subscribe to `capability.query`.
- Subscription serves reply on the request's `reply_inbox`.

### `roles/orchestrator/role.yaml` (bump to v3.0.0-beta1)

- `version: "3.0.0-beta1"` — beta because `can_route: true` is
  retained for backward compat in Phase 1.
- `purpose:` updated narrative: "Capability catalog specialist for
  skills + MCPs. Phase 1 ships read-only query."
- `allowed_actions:` adds `capability_query`, `capability_reply`.
- `policy_enabled: false` (Phase 1).

### Tests

- `tests/test_capability_index.py`:
  - Catalog rebuild on filesystem fixture (10 mock roles, 5 mock
    MCPs, 3 mock skills).
  - Query by `task_type` returns expected matches.
  - Query by `domain` (clinical_research / software_engineering / …)
    returns expected matches.
  - Cache invalidation on bus event.
- `tests/test_orchestrator_phase1.py`:
  - Boot path subscribes to `capability.query`.
  - End-to-end NATS round-trip: publish query → receive reply <
    100ms (CI budget).
  - Heartbeat-always-alive invariant (AoA-P1) preserved.

### Phase 1 does NOT include

- Recommendations. No marker parsing. No proposal pipeline.
- Gap analyser. No periodic scan.
- `can_route: false` flip. Phase 1 keeps backward compat.
- Marketplace integration. Defer to Phase 5.
- Dreamer M9 ingestion. Defer to Phase 6.

Phase 1 is **purely additive**: a new module + a new subject + a
role.yaml bump. Existing routing paths (orchestrator's
`[ROUTE:...]` + Assistant's `[PROPOSE_ROUTE]`) both continue to
work.

## What merges into Assistant (already done)

- Routing primitive `[PROPOSE_ROUTE]` (AoA-P2a, v0.3.26) ✅
- Queue-gated dispatch (AoA-P2b, v0.3.27) ✅
- Loop-guard hardening (PR-V6b) ✅ — already at the routing-
  primitive level; protects `[PROPOSE_ROUTE]` automatically
- Default target = Assistant (AoA-P1) ✅
- `can_route: true` on Assistant role.yaml v2.1.0 ✅

Nothing new to merge — AoA already has the pieces.

## What's new for orchestrator (full module map)

| Module | Phase | Purpose |
|---|---|---|
| `acc/capability_index.py` | 1 | Catalog + Redis cache |
| `acc/signals.py` (extend) | 1 | New subjects |
| `acc/agent.py` (extend) | 1 | Orchestrator boot path |
| `roles/orchestrator/role.yaml` | 1 (beta) → 3 (final) | v3.0.0-beta1 → v3.0.0 |
| `acc/cognitive_core.py` (extend) | 2 | Parse `[RECOMMEND_*]` markers |
| `acc/assistant_proposal.py` (extend) | 2 | New proposal kinds `recommend_skill` / `recommend_mcp` |
| `acc/gap_analyser.py` | 3 | Periodic scan loop |
| `acc/capability_index.py::hub_fetch_mirror` | 5 | Marketplace catalog ride |
| `acc/capability_index.py::ingest_dreamer_skills` | 6 | Dreamer M9 ride |
| `docs/ROLE_orchestrator.md` | 1 | Repurpose narrative + deprecation timeline |

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Catalog grows large → query latency creeps up | Redis cache with 24h TTL; sub-linear scan > 1k entries; embedding-keyed indexes |
| Single-replica bottleneck | NATS queue-group lets us spin replicas when load demands; Phase 1 single-replica is fine |
| Recommendations spam Compliance queue (Phase 2+) | Rate-limit ≤ 1 recommendation/role/24h; operator-tunable; honour SIP `drift_cap` |
| Drift between catalog + actual installed state | Bus invalidation on role_assign / role_update; filesystem-watch backup |
| Migration breaks operators emitting `[ROUTE:...]` | Phase 4 keeps parser for ≥ 2 versions with deprecation warning + auto-translate |
| LLM-backed rationale (Phase 2+) hallucinates | Rationale is display-only; the catalog match is deterministic; operator sees both before approving |
| Per-operator scope leakage (post-AoA-P5b) | Catalog partitioned by `operator_id` once TUI auth lands; Phase 1 collective-global |
| Cold-start recommendation accuracy poor | Phase 3 ships conservative thresholds; SIP bandit opt-in only after sufficient signal |

## Follow-on proposals (spawn after Phase 1 lands)

- `20260601-orchestrator-recommendation-markers` (Phase 2)
- `20260601-orchestrator-gap-analyser-loop` (Phase 3)
- `20260601-orchestrator-route-marker-deprecation` (Phase 4)
- `20260601-orchestrator-marketplace-mirror` (Phase 5)
- `20260601-orchestrator-dreamer-m9-ingest` (Phase 6)
- `20260601-orchestrator-route-marker-removal` (Phase 7)

## Linked

- Closes followup: `Notes/.../ACC Openspec/37 no orig Spec - Orchestrator routing superseded by AoA gatekeeper - followup - 20260531.md`
- Owns routing now: `20260530-assistant-agent-of-agents`
- Phase 5 ride: `20260531-acc-role-package-format`
- Phase 6 ride: `20260530-acc-dreaming-agent`
- SIP opt-in: `20260530-acc-self-improvement-policy-gradient`
- Compliance pane multi-kind consolidation: followup #39
- Historic origin: note 25 `OpenSpec — Orchestrator routing (PR-V6 - V6b) (v0.3.11)`
- Brainstorm: `Notes/.../ACC-Orchestrator-Repurpose/Orchestrator as Skills & MCP specialist — brainstorm.md`
