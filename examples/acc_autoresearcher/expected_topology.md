# Expected cluster topology — phase-by-phase reference

Reference for the operator running the autoresearcher demo.  If
the live TUI deviates from any of the snapshots below, that's the
bug to file.

All times are wall-clock and approximate; latency depends on the
backing LLM + the live web (browser-harness can take 30 s+ on
heavy sites).  The structural shape is the contract.

---

## Phase 0 — plan submitted (T+0s)

```
▼ Clusters: 0 (Σ 0 agents)
```

Comms screen (4): one PLAN entry visible in `signal_flow_log`.
Performance screen (5): six research_* agent rows in
REGISTERING/ACTIVE state, queue depth 0.

---

## Phase 1 — planner running (T+~5s)

```
▼ Clusters: 1 (Σ 1 agents)
  c-plan1234 · research_planner · 1 agents · fixed strategy, count=1
    ● research_planner-aaa · skill:plan_outline · step 3/6 · running
```

What this proves:

* `fixed: 1` estimator strategy translates to a 1-member cluster.
* Planner's `skill_in_use` reads `plan_outline` (D4 stub
  registered + persona's default_skills points at it).

Comms screen: `step_progress` shows
`plan: RUNNING, economics: PENDING, competitive: PENDING, …`.

---

## Phase 2 — economics + competitive in parallel (T+~30s)

```
▼ Clusters: 3 (Σ 4 agents)
  c-plan1234 · research_planner · 1 agents · fixed strategy, count=1
    ● research_planner-aaa · skill:plan_outline · step 6/6 · complete
  c-econ5678 · research_economist · 2 agents · 5200 tokens, +1 difficulty
    ● research_economist-bbb · skill:market_sizer · step 2/6 · running
    ● research_economist-ccc · skill:market_sizer · step 1/6 · running
  c-comp9012 · research_competitor · 2 agents · 4800 tokens, +1 difficulty
    ● research_competitor-ddd · skill:competitor_profile · step 2/6 · running
    ● research_competitor-eee · skill:competitor_profile · step 3/6 · running
```

What this proves:

* Planner cluster stays visible (30s grace window).
* Economist + competitor clusters fan out per heuristic estimator
  (forecast + edge bumps for economist; architecture + edge bumps
  for competitor).
* Both run truly in parallel — no `depends_on` between them.
* Performance screen: capability_stats shows
  `mcp:web_browser_harness.browse` and
  `mcp:web_search_brave.search` invocations climbing.

---

## Phase 3 — strategy synthesising (T+~120s)

```
▼ Clusters: 1 (Σ 1 agents)
  c-strat3456 · research_strategist · 1 agents · fixed strategy, count=1
    ● research_strategist-fff · skill:report_drafter · step 4/6 · running
```

What this proves:

* Economist + competitor clusters dropped from the panel after
  their 30s grace window.
* Strategist runs after both predecessors COMPLETE — `depends_on`
  is honoured.

---

## Phase 4 — synthesize ⇄ critique (T+~180s, may iterate)

```
▼ Clusters: 1 (Σ 1 agents)
  c-syn7890 · research_synthesizer · 1 agents · fixed strategy, count=1
    ● research_synthesizer-ggg · skill:report_drafter · step 5/6 · running
                                                       (iteration 1/3)
```

After synthesizer completes:

```
▼ Clusters: 1 (Σ 1 agents)
  c-crit1234 · research_critic · 1 agents · fixed strategy, count=1
    ● research_critic-hhh · skill:critic_verdict · step 4/6 · running
```

If the critic emits `NEEDS_REVISE`:

```
▼ Clusters: 1 (Σ 1 agents)
  c-syn7890 · research_synthesizer · 1 agents · fixed strategy, count=1
    ● research_synthesizer-ggg · skill:report_drafter · step 1/6 · running
                                                       (iteration 2/3)
```

Note: same `cluster_id` carries through iterations; only the
internal `task_id` changes (the panel renders `iteration n/N`
because PR-E1 puts iteration_n + max_iterations on the wire).

What this proves:

* Iteration loop (E1) is wired end-to-end: critic verdict
  reaches the arbiter, the synthesize step re-runs with the
  critique appended.
* Critic's `web_fetch` invocations show up on the Performance
  screen — these are the citation re-fetches `verify.sh`
  cross-references.

---

## Phase 5 — terminal (T+~300s, depends on iteration count)

```
▼ Clusters: 0 (Σ 0 agents)
```

Comms screen: every step COMPLETE; `step_progress` map shows
`plan: COMPLETE, economics: COMPLETE, competitive: COMPLETE,
strategy: COMPLETE, synthesize: COMPLETE, critique: COMPLETE`.

Performance screen: capability_stats shows the run total —
typically dozens of `mcp:web_search_brave.search`, similar number
of `mcp:web_fetch.fetch`, fewer of `mcp:web_browser_harness.browse`
(heavier per-invocation), and one `skill:critic_verdict` per
critic invocation.

The report file lands at:

```
runs/<topic-slug>-<YYYYMMDD>/agentic_ai_strategy_report.md
```

---

## Anti-checks — things that would indicate a regression

* Cluster panel header shows `Clusters: 0` while a step is
  mid-flight → cluster_id propagation regressed.  Check
  `acc/agent.py:_handle_task` echoes inbound cluster_id.
* `skill_in_use` column always says `?` → step_label
  convention regressed in `acc/capability_dispatch.py`.
* Synthesize step transitions COMPLETE without a critic
  cluster ever appearing → `depends_on` regressed in
  PlanExecutor or critique step's role mis-resolved.
* Critic cluster has 2+ members → critic role.yaml
  `estimator.fixed.count` drifted; check Cat-A A-019 logs.
* Members report `blocked: true, block_reason: A-018` → role
  doesn't permit the MCP it tried to invoke.  Most often:
  synthesizer trying to call browser-harness (intentionally
  forbidden — it has MEDIUM ceiling).
* iteration_n on TASK_ASSIGN payload increments WITHOUT a critic
  having published NEEDS_REVISE → bug in
  `_maybe_reissue_for_revise`.
* `verify.sh` reports coverage_rate=0 despite a populated report
  → critic isn't actually re-fetching cited URLs.  Check the
  critic's system_prompt + capability_dispatch.invocations.
