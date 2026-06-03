# Tasks — `20260531-role-proposal-orchestrator-skills-mcp-specialist`

## Phase 1 — catalog + query (read-only, additive)

- [ ] `acc/capability_index.py` — `CapabilityIndex` class:
  - Filesystem scan on init (`acc-config.yaml` + `mcps/*.yaml` +
    `roles/*/role.yaml`).
  - `query(CapabilityQuery) → CapabilityReply` — deterministic.
  - Redis cache, 24h TTL.
  - `subscribe_invalidation` listens on `subject_role_assign` +
    `subject_role_update`.
  - SIGHUP triggers `rebuild()`.
- [ ] `acc/signals.py` — `subject_capability_query(cid)`,
      `subject_capability_recommend(cid)` (defined but unused in
      Phase 1).
- [ ] `acc/agent.py` — orchestrator-role boot hook instantiates
      `CapabilityIndex` and subscribes to `capability.query`. Subscription
      handler decodes msgpack, calls `index.query`, replies on the
      request's `reply_inbox`.
- [ ] `roles/orchestrator/role.yaml` — bump to `v3.0.0-beta1`:
  - `purpose:` rewritten ("Capability catalog specialist…")
  - `allowed_actions:` += `capability_query`, `capability_reply`
  - `policy_enabled: false`
  - **Phase 1 retains `can_route: true`** for backward compat
- [ ] `docs/ROLE_orchestrator.md` — repurpose narrative,
      Phase 1–7 timeline, `[ROUTE:...]` deprecation note.
- [ ] Tests:
  - `tests/test_capability_index.py` (catalog correctness,
    query shapes, cache invalidation).
  - `tests/test_orchestrator_phase1.py` (boot path, NATS
    round-trip, heartbeat-always-alive).

## Phase 2 — recommendation markers — DEFERRED

Gates on Phase 1 landed + reviewed.

- [ ] `acc/cognitive_core.py` — parse `[RECOMMEND_SKILL:role:skill:
      rationale]` and `[RECOMMEND_MCP:role:mcp:rationale]` when role
      == orchestrator.
- [ ] `acc/assistant_proposal.py` — new `AssistantProposal` kinds:
      `recommend_skill`, `recommend_mcp`. Dispatch on approve =
      `role_update` adding the skill/MCP to the target role's
      `role.yaml`.
- [ ] Compliance pane gets the new oversight kind rendered (closes
      part of followup #39).
- [ ] LLM rationale generation path for orchestrator (hybrid mode).
- [ ] Tests: marker parsing, proposal dispatch, role.yaml mutation
      round-trip.

## Phase 3 — gap analyser — DEFERRED

- [ ] `acc/gap_analyser.py` — periodic loop (default 30 min, matches
      PR-Z3e). Scans recent task patterns + role assignments +
      catalog; emits `[RECOMMEND_*]` markers.
- [ ] Rate limit ≤ 1 recommendation/role/24h.
- [ ] Honour SIP `drift_cap` when bandit is on (Phase 3 flips
      `policy_enabled: true` if approval-signal threshold met).
- [ ] Flip `roles/orchestrator/role.yaml` to v3.0.0 (final):
      `can_route: false`.

## Phase 4 — `[ROUTE:...]` deprecation — DEFERRED

- [ ] Cognitive_core logs one-line deprecation warning when any
      agent emits `[ROUTE:...]`; auto-translates to
      `[PROPOSE_ROUTE:...]` via the Assistant queue.
- [ ] Documentation update.

## Phase 5 — marketplace catalog mirror — DEFERRED

Gates on `20260531-acc-role-package-format` Phase 4 (Hub MVP).

- [ ] `CapabilityIndex.hub_fetch_mirror()` — periodically pulls
      `acc-hub` package index; surfaces "available but not installed"
      packages in catalog queries.
- [ ] Operator can ask Assistant "any new clinical-research skills
      on the hub?"; chain resolves through orchestrator.

## Phase 6 — dreamer M9 ingestion — DEFERRED

Gates on `20260530-role-proposal-dreamer-agent` Phase 5 (M9 skill library
distillation).

- [ ] Dreamer-distilled skills flow into orchestrator's catalog via
      a new bus subject `acc.{cid}.dream.distilled_skills`.
- [ ] Catalog tags distilled skills with provenance.

## Phase 7 — remove `[ROUTE:...]` parser — DEFERRED

Gates on Phase 4 + ≥ 2 minor versions since deprecation warning.

- [ ] Drop the `[ROUTE:...]` regex from `cognitive_core`.
- [ ] Drop the auto-translate shim.
- [ ] Documentation update.

## Follow-on proposals to spawn after Phase 1 lands

- [ ] `20260601-orchestrator-recommendation-markers`
- [ ] `20260601-orchestrator-gap-analyser-loop`
- [ ] `20260601-orchestrator-route-marker-deprecation`
- [ ] `20260601-orchestrator-marketplace-mirror`
- [ ] `20260601-orchestrator-dreamer-m9-ingest`
- [ ] `20260601-orchestrator-route-marker-removal`
