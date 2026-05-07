# Expected cluster topology — what to look for during the demo

Reference for the operator running the scenario. If the live TUI
deviates from any of the snapshots below, that's the bug to file.

All times are wall-clock and approximate; latency depends on the
backing LLM. The structural shape is the contract.

---

## Phase 0 — plan submitted (T+0s)

```
▼ Clusters: 0 (Σ 0 agents)
```

Comms screen (4): one PLAN entry visible in `signal_flow_log`.
Performance screen (5): three coding_agent_* agent rows in
REGISTERING/ACTIVE state, queue depth 0.

---

## Phase 1 — design + deps in flight (T+~2s)

```
▼ Clusters: 2 (Σ 2 agents)
  c-arch1234 · coding_agent_architect · 1 agents · fixed strategy, count=1
    ● coding_agent_arch-aaa · skill:code_review   · step 4/6 · running
  c-deps5678 · coding_agent_dependency · 1 agents · fixed strategy, count=1
    ● coding_agent_dep-bbb  · skill:dependency_audit · step 3/6 · running
```

What this proves:

* `fixed: 1` estimator strategy translates to a 1-member cluster.
* Two clusters run truly in parallel (no dependency between them).
* `skill_in_use` column reads from the most recent
  TASK_PROGRESS step_label — different per persona.

Comms screen: `step_progress` map for the plan shows
`design: RUNNING, deps: RUNNING, implement: PENDING, test:
PENDING, review: PENDING`.

---

## Phase 2 — design done; implement spawning (T+~12s)

```
▼ Clusters: 2 (Σ 4 agents)
  c-arch1234 · coding_agent_architect · 1 agents · fixed strategy, count=1
    ● coding_agent_arch-aaa · skill:code_review   · step 6/6 · complete
  c-impl9012 · coding_agent_implementer · 3 agents · 6300 tokens, +1 difficulty
    ● coding_agent_impl-ccc · skill:code_generation · step 1/6 · running
    ● coding_agent_impl-ddd · skill:code_generation · step 1/6 · running
    ● coding_agent_impl-eee · skill:code_generation · step 1/6 · running
```

What this proves:

* The architect cluster stays visible (grace window — 30 s after
  `finished_at` it disappears).
* The implementer cluster expanded to 3 members from the
  heuristic estimator (PR #27 path).
* Same `cluster_id` shared by all three impl members.

Comms screen: knowledge_feed shows one entry from architect with
`tag=code_patterns, type=draft_interface`.

---

## Phase 3 — implement done; test + review parallel (T+~45s)

```
▼ Clusters: 3 (Σ 4 agents)
  c-impl9012 · coding_agent_implementer · 3 agents
    ● coding_agent_impl-ccc · skill:code_generation · step 6/6 · complete
    ● coding_agent_impl-ddd · skill:code_generation · step 6/6 · complete
    ● coding_agent_impl-eee · skill:code_generation · step 6/6 · complete
  c-test3456 · coding_agent_tester · 2 agents · 4200 tokens
    ● coding_agent_test-fff · skill:test_generation · step 2/6 · running
    ● coding_agent_test-ggg · skill:test_execution  · step 4/6 · running
  c-rev7890 · coding_agent_reviewer · 1 agents · fixed strategy, count=1
    ● coding_agent_rev-hhh  · skill:code_review     · step 3/6 · running
```

What this proves:

* Aggregation at the arbiter: implement cluster transitioned to
  COMPLETE only after ALL three members reported.
* Two downstream steps fire in parallel (test + review) because
  both have `depends_on` resolved.
* Test cluster shows skill heterogeneity within one cluster —
  one member generating, another running. This emerges from the
  members entering different cognitive-step boundaries; both
  carry the same `cluster_id`.

Comms screen: `step_progress` map now `design: COMPLETE,
implement: COMPLETE, deps: COMPLETE, test: RUNNING, review:
RUNNING`.

---

## Phase 4 — terminal (T+~60s)

```
▼ Clusters: 0 (Σ 0 agents)
```

The 30-second grace window has elapsed for every cluster.
Performance screen shows the post-run telemetry: per-skill
invocation counts, success rates, no error tail.

Comms screen: PLAN DAG fully rendered, every step COMPLETE.
Episode log (Performance → CAPABILITIES panel) shows the new
episode_ids the EVAL_OUTCOME emissions promoted.

---

## Anti-checks — things that would indicate a regression

* Cluster panel header shows `Clusters: 0` while a plan is mid-flight.
  → The `cluster_id` propagation regressed. Check
  `acc/agent.py:_handle_task` echoes `inbound_cluster_id`.
* `skill_in_use` column always says `?`.
  → The `Calling skill:<name>` step-label convention regressed in
  `acc/capability_dispatch.py`. Search for `step_label` emissions.
* All five steps run sequentially even though `depends_on` allows
  parallelism.
  → `_dispatch_ready_steps` regressed; or the test cluster did not
  fan out (estimator config error).
* Reviewer cluster spawns 2+ members instead of 1.
  → `coding_agent_reviewer` role.yaml `estimator.fixed.count`
  drifted; check Cat-A A-019 logs.
* Members report `blocked: true, block_reason: A-017 forbidden skill`.
  → The persona's `allowed_skills` list is missing the skill the
  prompt tells the LLM to invoke.
