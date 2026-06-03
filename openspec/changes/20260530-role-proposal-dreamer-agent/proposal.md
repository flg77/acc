# OpenSpec — Agent dreamer (out-of-band memory consolidation)

| Field | Value |
|---|---|
| Change ID | `20260530-role-proposal-dreamer-agent` |
| Status | Proposed (Phase 1 ready to implement) |
| Sibling | `20260530-acc-self-improvement-policy-gradient` (drift diagnostics feed the bandit) |
| Companion | `20260530-role-proposal-assistant-agent-of-agents` (queue-gated mutations reuse the proposal pipeline) |
| Notes mirror | `Notes/Development/AgenticCellCorpus/ACC Openspec/20260530-role-proposal-dreamer-agent — OpenSpec (proposed).md` |
| Brainstorm | `Notes/Development/AgenticCellCorpus/ACC-Dreaming/Agent dreamer — brainstorm.md` |

## Problem statement

ACC's per-agent reflection loop (PR-MEM2) consolidates one agent's
episodes in process — nothing today optimises memory **across**
agents, deduplicates redundancy at the collective level, prunes
stale episodes by access frequency, detects memory poisoning, or
generates the synthetic replay variation that biological sleep
contributes to learning.

**Industry context (2026-05-06):** Anthropic shipped *Dreaming* on
Claude Managed Agents at Code with Claude. Inputs: pre-existing
memory store + up to 100 session transcripts. Outputs: a new
immutable memory store (input never modified). Detects recurring
mistakes, convergent workflows, shared preferences. Harvey reports
~6× task-completion improvement. **The lock-in is the runtime
(~$0.08/hr) + the proprietary store format + the Anthropic-only
audit surface — not the algorithm**, which is well-trodden
(Reflexion / Generative Agents / Voyager). ACC's dreamer is the
open, self-hosted, governance-bounded equivalent.

A dedicated **dreamer agent** runs out-of-band on a configurable
cadence and consolidates memory at the collective scope. Phase 1
is **observation-only** — diagnostics, never mutations — so the
operator can see what the dreamer would do before granting it
authority. This mirrors Anthropic's "review before attach" gate.

## Four lifted safety properties (table stakes — from Anthropic's design)

These are not optional in any phase:

1. **Immutable input store.** A dream pass never mutates raw
   `episodes`. It produces a new versioned `memory_notes` snapshot.
   The raw table is append-only.
2. **Read-only at the consume side.** Agents reading memory_notes
   cannot write them; only the dreamer (or operator) writes. Also
   the prompt-injection rail.
3. **Review-before-attach.** New memory_notes versions are
   *proposed* (Phase 2+ via AoA-P2b queue), not auto-attached.
   Phase 1 satisfies this trivially (observation-only).
4. **Audit trail per write.** Every mutation publishes a
   `DREAM_AUDIT` event with old hash + new hash + diff stats; OTel
   pipeline promotes to MLflow span events automatically.

Plus one concrete budget number lifted directly: **100-session
input cap** per dream pass (matches Anthropic; matches Generative
Agents' 100-event reflection trigger — convergent design).

## Reuses existing primitives (no reinvention)

| Need | Existing primitive |
|---|---|
| Episode storage | LanceDB `episodes` table (`acc/backends/vector_lancedb.py`); `acc/cognitive_core.py::_persist_episode` |
| Consolidation loop pattern | `acc/memory_reflection.py`; `acc/agent.py::_reflection_loop` |
| Greedy clustering | `_cluster_episodes` (cosine ≥ 0.6) |
| Memory-notes cache | Redis `acc:{cid}:memory_notes:{role}` |
| Drift centroids | `acc/cognitive_core.py::_compute_drift`; Redis `acc:{cid}:{agent_id}:centroid` |
| Scheduler | `acc/scheduler/` + PR-O systemd recipes |
| Periodic-role pattern | `compliance_officer` self-challenge (PR-Z3e) |
| Queue-gated mutation | AoA-P2b `AssistantProposal` pipeline |
| Heartbeat-always-alive | AoA-P1 dormant-watcher invariant |
| Telemetry surface | MLflow Trace UI span events (20260527-mlflow-otel-telemetry Phase 4) |

## Phase summary

| Phase | Status | Mechanisms | Authority | Output |
|---|---|---|---|---|
| 1 | Proposed | M5 (cross-agent centroid recompute) · M8 (poisoning detection) · M10 (markdown export) · M11 (convergent-workflow detection) · M12 (preference extraction) | Observation-only | `DREAM_REPORT` on `acc.{cid}.dream.report`; rendered on Compliance pane; mirrored to `dreams/*.md` |
| 2 | Deferred | M1 (dedup) · M2 (LRU pruning) | Queue-gated via AoA-P2b proposal | Each batch lands as `[PROPOSE_DREAM_BATCH:kind:n]`; operator approves |
| 3 | Deferred | M3 (chain compression) · M4 (episode → memory_note promotion) | Autonomous within Cat-A/B/C | `DREAM_AUDIT` events; OTel pipeline promotes to MLflow span events |
| 4 | Deferred (gates on AoA-P3b live + multi-domain receptors) | M6 (cross-pollination across sub-collectives) | Autonomous + receptor consent | Cross-published memory_notes with provenance |
| 5 | Deferred (gates on operator review of Phase 1–3 telemetry) | M7 (synthetic adversarial replay) · M9 (skill library distillation) | Always queue-gated regardless of mode; Cat-A HIGH_CONSEQUENCE | Synthetic episodes + skill entries |

## Phase 1 design decisions (bootstrap defaults)

The brainstorm's seven open questions are resolved for Phase 1 as
follows. Each decision is overridable in Phase 2+ once the operator
has read live `DREAM_REPORT` output.

1. **Cadence** — nightly cron via existing `acc/scheduler` (default
   03:00 local). PR-O systemd recipe ships alongside.
2. **Scope** — per-collective (the hub). Sub-collectives are
   indirectly covered via AoA-P3b registry; per-sub-collective passes
   land in Phase 4.
3. **Authority** — observation-only. No mutations to `episodes` or
   live `memory_notes`. Mirrors Anthropic's review-before-attach.
4. **Mechanisms** — M5 + M8 + M10 + M11 + M12 (all observation-only;
   M10 adds the markdown-export UX so operators get the Anthropic
   `grep`-able surface without the vendor runtime).
5. **Compute budget** — 50k tokens / pass cap; vector-scan rate
   limit 100 rows/sec; cheap fast model for M11/M12 LLM
   summarisation; **100-session input cap (lifted from Anthropic)**;
   M5 + M8 + M10 are pure-vector / file-IO (zero LLM tokens).
6. **Identity scope** — one dreamer per hub. Per-operator boundaries
   become enforceable when AoA-P5b TUI auth lands; until then the
   dreamer reads with the hub's service identity and tags reports
   with `operator_id: "default"`.
7. **Citations** — brainstorm flags ~5 arxiv IDs needing
   verification; spot-check before any of them ship in user-facing
   docs.

## Phase 1 deliverables

- New `dreamer` role under `roles/dreamer/role.yaml`:
  - `skills: []`, `mcps: []`, `workspace_access: false`
  - `policy_enabled: false` (no SIP knobs to tune in Phase 1)
  - Heartbeat-always-alive invariant per AoA-P1
- New module `acc/dreamer.py`:
  - `DreamerHarness` class — instantiated on agent boot when
    `self.config.agent.role == "dreamer"`
  - `dream_pass(now)` — entry point invoked by the scheduler;
    enforces the **immutable-input rail** (never writes to
    `episodes`) + **100-session cap** on transcript input
  - `_centroid_recompute()` (M5) — reads per-agent centroids from
    Redis, computes hub-level role centroids, returns drift deltas
    per agent
  - `_poisoning_scan(k=200)` (M8) — samples top-k highest-retrieved
    episodes, computes embedding density per cluster, flags outliers
    in low-density regions with high retrieval frequency
  - `_export_markdown(out_dir)` (M10) — mirrors current memory_notes
    cortex tier to `out_dir/*.md`, one file per cluster, slug-named;
    pure read; operator-greppable
  - `_convergent_workflows(k=100)` (M11) — scans last K sessions for
    `task_type` + tool-call-sequence patterns shared across ≥ K
    distinct agents
  - `_preference_extraction()` (M12) — aggregates EVAL_OUTCOME +
    operator-approval signals by `operator_id` (Phase 1 single-op,
    so all under `"default"`); surfaces `PREFERENCE_HINT` candidates
- New subject helper `subject_dream_report(cid)` →
  `acc.{cid}.dream.report` in `acc/signals.py`
- `DREAM_REPORT` payload:
  ```json
  {
    "ts": 1780000000.0,
    "pass_id": "dream-<uuid>",
    "scope": "collective",
    "operator_id": "default",
    "input_sessions": 87,
    "input_cap": 100,
    "centroid_drift": {
      "<agent_id>": { "role": "...", "drift_delta": 0.12, "above_cap": false }
    },
    "poisoning_candidates": [
      { "episode_id": "...", "agent_id": "...", "retrieval_count": 47, "density_score": 0.03 }
    ],
    "convergent_workflows": [
      { "task_type": "...", "tool_seq": [...], "n_agents": 4 }
    ],
    "preference_hints": [
      { "kind": "model_temperature", "value": 0.3, "support": 12 }
    ],
    "markdown_export_dir": "/var/lib/acc/dreams/2026-05-30T03-00",
    "tokens_used": 0,
    "wall_time_s": 4.2
  }
  ```
- `acc/scheduler/` YAML entry: `dream-pass` job, default cron
  `0 3 * * *`, target role `dreamer`
- Compliance pane gains a `DREAM_REPORT` tab showing the last 7
  reports (lightweight read of `subject_dream_report` history)
- Tests: `tests/test_dreamer_phase1.py` covering:
  - `_centroid_recompute` produces drift deltas matching a fixture
  - `_poisoning_scan` flags planted outlier episodes
  - `dream_pass` publishes a well-formed `DREAM_REPORT`
  - Scheduler entry parses + dispatches the pass
  - Token cap + scan rate limit honoured

## Six rails (carried from SIP, applied to the dreamer)

1. **Reward-hacking via routing** — Phase 1 doesn't write rewards; rail
   not active until Phase 3+. When Phase 3 lands, dreamer mutations
   that move a knob get the delegator credit-share cap.
2. **Drift-as-constraint** — dreamer's M5 output is *diagnostic*;
   it never reduces drift directly. SIP-P2's bandit reads
   `DREAM_REPORT` centroid deltas as one more reward signal.
3. **Policy ↔ Cat-C race** — dreamer's queue-gated mutation cadence
   (Phase 2+) is bounded by `dream_update_every_n_passes` (default 5),
   slower than Cat-C cadence.
4. **Memory ↔ policy disagreement** — dreamer operates on memory tier
   only; never touches θ vectors. Disagreement surfaces as a `flag`
   field on the report, not a resolution.
5. **Multi-agent credit** — dreamer reads cross-agent state but only
   produces aggregated metrics in Phase 1. Multi-agent attribution
   for any future dreamer-induced mutation gates on SIP-P4.
6. **Frozen-in-AUTO** — when collective operating mode is
   `MODE_AUTO`, the dreamer still runs (it's read-only in Phase 1)
   but Phase 2+ queue-gated mutations are skipped, mirroring SIP's
   `observe_task` no-op rule.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| **Consolidating the wrong thing (Iacono)** — consensus-as-truth | **Dream output is additive; raw episodes preserved (immutable-input rail); operator can roll back to any prior memory_notes version** |
| **Vendor regression risk (Wąsowski)** — getting stuck on a paid runtime once 6× economics land | **ACC dreamer is operator-owned; M10 markdown export makes the cortex tier portable; substrate-agnostic LanceDB+Redis** |
| Compute cost grows with corpus | Token cap (Phase 1: ~0 tokens; Phase 3+: 50k/pass); **100-session input cap (Anthropic precedent)**; sub-linear sampling > 100k episodes |
| Privacy across operators (post-AoA-P5b) | Phase 1 tags `operator_id: "default"`; per-operator scope lands when TUI auth lands |
| Drift dampening from over-consolidation | `policy_drift_cap` from SIP-P2 applies; dreamer flags + refuses to consolidate when consolidation would push role drift above cap (Phase 2+) |
| Cat-C race (memory ↔ policy rail 3) | Dreamer mutations rate-limited relative to Cat-C cadence (Phase 2+) |
| Dreamer dying silently | Heartbeat-always-alive invariant extended to dreamer role; non-heartbeating dreamer surfaces on Soma + Compliance |
| Operator surprise on first run | Phase 1 is observation-only by design; first N reports rendered as preview before any Phase 2 mutation lands |
| Memory poisoning by the dreamer itself | Phase 5 (M7) always queue-gated regardless of mode; daily audit diff `(memory_notes_yesterday, memory_notes_today)` |

## Standing-rule cons → follow-on proposals (after Phase 1 lands)

Per the discipline established for AoA + SIP, Phase 1 spawns
sibling proposals for the open cons:

- `20260601-acc-dreaming-compliance-pane-surface` — UI for
  `DREAM_REPORT` rendering on the Compliance pane (Phase 1 ships the
  data; this proposal ships the UI polish).
- `20260601-acc-dreaming-token-cap-autotuning` — adapt the per-pass
  token budget based on corpus growth rate.
- `20260601-acc-dreaming-disabled-fallback-warning` — when the
  dreamer has been dormant for N days, surface a Soma warning so
  operators know they're running without consolidation.
- `20260601-acc-dreaming-per-operator-scope` — when AoA-P5b TUI auth
  lands, scope the dreamer's reads to `operator_id`-tagged episodes.
- `20260601-acc-dreaming-markdown-import` — accept Anthropic
  Managed-Agents-format memory stores as import source for
  operators migrating off the vendor runtime; closes the
  export/import loop with M10.

## Linked

- Sibling: `20260530-acc-self-improvement-policy-gradient` — the
  bandit consumes the dreamer's centroid-drift diagnostics.
- Companion: `20260530-role-proposal-assistant-agent-of-agents` — Phase 2+
  mutations land via the AoA-P2b proposal queue.
- Telemetry: `20260527-mlflow-otel-telemetry` — `DREAM_AUDIT` events
  (Phase 3+) promote to MLflow span events automatically.
- Memory primitives: PR-MEM1/2/3 — episodes, reflection loop,
  memory_notes hot-cache (the building blocks the dreamer reuses).
- Periodic-role pattern: PR-Z3e compliance officer self-challenge.
