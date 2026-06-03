# Tasks — `20260530-role-proposal-dreamer-agent`

## Phase 1 — observation-only (M5 + M8 + M10 + M11 + M12)

> v2 update: expanded to match Anthropic Dreaming's three pattern
> kinds (mistakes/convergence/preferences) + add markdown export
> for operator parity with Claude Managed Agents. Four lifted
> safety properties enforced from day one: immutable input,
> read-only consume, review-before-attach (trivially satisfied
> in observation-only mode), audit trail. 100-session input cap.

- [ ] `roles/dreamer/role.yaml` — new role; `skills: []`, `mcps: []`,
      `workspace_access: false`, `policy_enabled: false`,
      heartbeat-always-alive per AoA-P1.
- [ ] `acc/dreamer.py` — `DreamerHarness` class with:
  - `__init__(self, agent, cid, ...)` wired from `acc/agent.py` on
    role match.
  - `dream_pass(now)` — entry point; calls M5 + M8; publishes
    `DREAM_REPORT`; enforces token cap (0 in P1) + scan rate limit.
  - `_centroid_recompute()` — reads `acc:{cid}:*:centroid` via Redis
    SCAN; computes per-role hub centroid; returns drift deltas.
  - `_poisoning_scan(k=200)` — samples top-k high-retrieval episodes
    via LanceDB; computes embedding density; flags low-density +
    high-retrieval outliers.
  - `_export_markdown(out_dir)` (M10) — mirrors current
    memory_notes cortex tier to `out_dir/*.md`, one file per
    cluster, slug-named; pure read; operator-greppable. Matches
    Anthropic's `descent-playbook.md` UX.
  - `_convergent_workflows(k=100)` (M11) — scans last K sessions
    (input-session cap enforced) for `task_type` + tool-call-sequence
    patterns recurring across ≥ K_agents distinct agents.
  - `_preference_extraction()` (M12) — aggregates EVAL_OUTCOME +
    operator-approval signals; surfaces `PREFERENCE_HINT`
    candidates. Tagged `operator_id: "default"` until AoA-P5b.
  - **Immutable-input rail enforced** — module-level assertion:
    no write path to `episodes` table; only memory_notes writes
    allowed (and gated by phase).
  - **100-session input cap** — concrete constant honoured by
    `dream_pass` and `_convergent_workflows`.
- [ ] `acc/signals.py` — `subject_dream_report(cid)` →
      `acc.{cid}.dream.report`.
- [ ] `acc/scheduler/` — `dream-pass` YAML entry; default cron
      `0 3 * * *`; targets role `dreamer`.
- [ ] `acc/agent.py` — boot hook: if `role == "dreamer"`, instantiate
      `DreamerHarness` and subscribe to the scheduler dispatch
      subject.
- [ ] PR-O systemd recipe — `docs/operator-dreamer-schedule.md`
      showing how to override the cron expression.
- [ ] Compliance pane TUI — minimal `DREAM_REPORT` tab reading the
      last 7 reports from the bus.
- [ ] Tests in `tests/test_dreamer_phase1.py`:
  - `_centroid_recompute` produces drift deltas matching fixture.
  - `_poisoning_scan` flags planted outlier episodes.
  - `_export_markdown` writes correctly-named files for fixture
    memory_notes; no LanceDB writes.
  - `_convergent_workflows` surfaces a planted-pattern workflow
    shared by ≥ 3 fixture agents.
  - `_preference_extraction` aggregates fixture EVAL_OUTCOME signals.
  - `dream_pass` publishes well-formed `DREAM_REPORT` payload with
    all six top-level fields.
  - **Immutable-input rail**: attempting an `episodes` write inside
    a dream pass raises; test asserts the guard fires.
  - **100-session cap honoured** — passing 200 fixture sessions to
    `dream_pass` clamps the input to 100.
  - Scheduler entry parses + dispatches the pass.
  - Scan rate limit honoured (100 rows/sec default).
  - Heartbeat-always-alive invariant: dreamer heartbeats during pass.

## Phase 2 — queue-gated mutations (M1 + M2) — DEFERRED

Lands after Phase 1 has produced ≥ 2 weeks of `DREAM_REPORT`
telemetry and the operator confirms the diagnostics are useful.

- [ ] M1 — episode deduplication; proposes `[PROPOSE_DREAM_BATCH:
      dedup:N]` to AoA-P2b queue.
- [ ] M2 — LRU pruning by retrieval frequency; `access_count` column
      added to LanceDB `episodes` schema (migration); proposes
      `[PROPOSE_DREAM_BATCH:prune:N]`.
- [ ] Six-rail enforcement: rate-limit relative to Cat-C cadence;
      frozen-in-AUTO; drift-cap refusal.

## Phase 3 — autonomous within Cat-A/B/C (M3 + M4) — DEFERRED

Gates on Phase 2 ≥ 4 weeks of clean queue-approved mutations.

- [ ] M3 — episode chain compression by `task_id`; `compressed_
      episodes` sibling table.
- [ ] M4 — episode → memory_note promotion at retrieval-count
      threshold.
- [ ] `DREAM_AUDIT` events on `acc.{cid}.dream.audit`; OTel pipeline
      promotes to MLflow span events.

## Phase 4 — cross-collective (M6) — DEFERRED

Gates on AoA-P3b sub-collective registry live + domain receptors
(ACC-11).

- [ ] M6 — cross-pollination; receptor-gated; provenance carried on
      cross-published memory_notes.

## Phase 5 — synthetic creative work (M7 + M9) — DEFERRED

Always queue-gated regardless of mode; Cat-A HIGH_CONSEQUENCE.

- [ ] M7 — synthetic adversarial replay; LLM-generated variant
      episodes; drives SIP bandit with synthetic rewards.
- [ ] M9 — skill library distillation; LLM-summarised procedural
      patterns; feeds acc-skills.

## Follow-on proposals to spawn after Phase 1 lands

- [ ] `20260601-acc-dreaming-compliance-pane-surface` — UI polish.
- [ ] `20260601-acc-dreaming-token-cap-autotuning` — adaptive budget.
- [ ] `20260601-acc-dreaming-disabled-fallback-warning` — Soma alert
      when dreamer dormant N days.
- [ ] `20260601-acc-dreaming-per-operator-scope` — scope reads to
      `operator_id`-tagged episodes once AoA-P5b lands.
- [ ] `20260601-acc-dreaming-markdown-import` — accept Anthropic
      Managed-Agents-format memory stores as import source for
      operators migrating off the vendor runtime; closes the
      export/import loop with M10.
