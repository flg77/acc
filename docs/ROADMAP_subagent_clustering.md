# Roadmap — Sub-agent Clustering Improvements

**Companion to:** `IMPLEMENTATION_subagent_clustering.md` (what
shipped), `SUBAGENT_COMMUNICATION.md` (today's contract),
`CODING_AGENT_SUBROLES.md` (persona library design).

This document forecasts what comes after PR #26–#30. Items are
ordered roughly by ROI × delivery cost, not by absolute desirability
— a few high-value items are far down the list because they need a
lot of plumbing.

> **Time horizon convention.**
> *Short* = 1–2 weeks; *Medium* = 1–3 months; *Long* = 3–9 months.

---

## Short-term (1–2 weeks)

### S1. Agent-side TASK_CANCEL handler

**State:** PR #30 ships the operator-side publish; the cooperative
checkpoint inside `CognitiveCore.process_task` is a small follow-up.

**Scope.**
* New subscriber on `acc.{cid}.task.cancel` in `acc/agent.py`.
* `cancel_event: asyncio.Event` plumbed into `process_task`.
* Step-boundary check in cognitive_core that raises
  `asyncio.CancelledError` with a pre-set
  `block_reason="cancelled"`.
* TASK_COMPLETE emitted with `blocked=True, block_reason="cancelled"`.

**ROI.** Closes the operator-cancel loop. ~150 LOC + 6 tests.

### S2. Coding-agent persona role files

**State:** designs documented in `CODING_AGENT_SUBROLES.md`; no role
directories exist yet.

**Scope.**
* Five new directories under `roles/`.
* Each carries `role.md`, `role.yaml`, `system_prompt.md`,
  `eval_rubric.yaml`.
* Update prompt-pane target dropdown defaults.
* Update `examples/coding_split_skills/` to be runnable.

**ROI.** Unlocks the demo. ~600 LOC of YAML/markdown + 4 tests
ensuring lint passes for every persona.

### S3. SubtraceModal in Performance screen

**State:** scoped out of PR #29.

**Scope.**
* `SubtraceModal(ModalScreen)` opens on a Performance row click.
* Renders the chronological TASK_PROGRESS history for one
  `(agent_id, task_id)`.
* Reuses the existing prompt-pane progress entry renderer for
  visual consistency.

**ROI.** "What did this sub-agent actually think about?" — one of
the most-requested operator surfaces during the persona design
review.

### S4. Mixed-payload knowledge-share assist

**State:** Pattern C (scratchpad rendezvous) works, but operators
have to compose the path by hand.

**Scope.**
* New helper `acc.cluster.scratchpad_key(cid, cluster_id, key)`.
* New convenience method `KnowledgeShare.publish_artefact(...)`
  that writes the blob to scratchpad + emits the matching
  KNOWLEDGE_SHARE in one call.

**ROI.** Reduces the cliff between Pattern B and Pattern C — every
persona authoring one becomes one line.

---

## Medium-term (1–3 months)

### M1. Mixed-role clusters

**State:** estimator + arbiter assume one role per cluster.

**Scope.**
* Estimator returns a heterogeneous member spec
  (`list[(role, skill_slice)]` instead of `(role, skill_mix)`).
* Arbiter's `_dispatch_cluster` publishes per-member
  `target_role` + `target_agent_id`.
* `slice_skill_mix` adapts to per-role caps.
* Cluster panel header shows mixed roles
  (`coding_agent_architect + 2× coding_agent_implementer`).

**ROI.** Removes the biggest design constraint of the current
implementation. Lets a single cluster hold one architect + N
implementers + a reviewer simultaneously.

**Cost.** ~800 LOC + a non-trivial test rewrite. Cat-A A-019 stays
correct (it gates per-(role, member-count) admission).

### M2. Cluster autoscaling mid-task

**State:** clusters are committed to N members at PLAN-step start.

**Scope.**
* New signal `CLUSTER_RESIZE` (arbiter → cluster) carrying a delta.
* Cooperative shrink: leaving members emit a "shutting down"
  TASK_PROGRESS, finish their current step, exit cleanly.
* Cooperative grow: new TASK_ASSIGN with same `cluster_id`
  injected into existing cluster; aggregator counts the new member
  before declaring the step done.

**ROI.** Long-running clusters (large refactors, big test suites)
adapt to changing load.

**Risk.** Aggregation logic gets harder. The ROI may be lower than
the cost for the foreseeable workloads.

### M3. LLM-decided estimator strategy

**State:** `module:` strategy hook exists; no reference LLM
estimator ships.

**Scope.**
* New `acc/estimator/llm_estimator.py` — calls a designated
  estimator-role agent (`role: cluster_estimator`) with the task
  description; the role replies with a structured
  `ClusterPlan`-shaped JSON.
* Cat-A A-019 still clamps the result.
* Caching by task description hash to avoid paying twice for
  identical workloads.

**ROI.** Estimators evolve as the model evolves. Heuristics
won't scale to operators who add new skills.

**Cost.** New role + cognitive_core integration. ~500 LOC.

### M4. Cluster panel — direct intervention surface

**State:** PR #29 panel is read-only; intervention happens via
slash commands.

**Scope.**
* Click a member row → context menu (Cancel member, Open
  subtrace, Bump priority).
* Drag-drop members between clusters (advanced; gated behind
  `mixed_role_clusters` capability).
* Inline cluster_id copy-button.

**ROI.** Reduces operator effort for routine cluster grooming.

### M5. Cancel-with-reason

**State:** TASK_CANCEL carries no reason today.

**Scope.**
* Optional `reason` field on the TASK_CANCEL payload.
* `/cancel <id> <reason text>` slash-command extension.
* Reason persisted to episode log + surfaced in Performance
  screen for post-hoc analysis.

**ROI.** Future Cat-C rule promotion: "tasks the operator
cancelled with reason X tend to be high-cost X-pattern" → arbiter
suggests skipping that pattern next time.

---

## Long-term (3–9 months)

### L1. Distributed cluster across collectives

**State:** clusters are intra-collective.

**Scope.**
* Cluster spans an edge corpus + a hub corpus via the existing
  ACC-9 bridge.
* Bridge subjects forward TASK_PROGRESS / TASK_COMPLETE with
  `cluster_id` intact.
* Cluster panel renders cross-collective members with a
  collective_id badge.

**ROI.** Edge picks up the work it can, hub absorbs the rest.
Matches the OpenShift Plus + RHACM scale story.

**Cost.** ~2000 LOC. ACC-9 bridge already carries the wire shape
for cross-cluster signal forwarding; the hard part is routing
cancel + aggregation across the bridge with consistent semantics
under partition.

### L2. Cluster-level Cat-C rule promotion

**State:** Cat-C promotion runs per-episode (single agent today).

**Scope.**
* Episodes carry `cluster_id` + `member_role_mix` so the
  promoter can identify "this collaboration shape worked well".
* New rule kind: `prefer_cluster(role_mix=...)` for downstream
  similar tasks.

**ROI.** Cluster optimisation becomes self-improving — the system
learns which decompositions worked.

### L3. Visual cluster builder

**State:** clusters are configured via role.yaml estimator blocks.

**Scope.**
* TUI screen 8 (or web UI) — drag-drop personas onto a cluster
  canvas, see live preview of TASK_ASSIGN payloads.
* Output is a `role.md` snippet the operator pastes into the
  persona's source.

**ROI.** Onboarding accelerator. Operators build new clusters
without learning the YAML schema.

**Cost.** Substantial. Probably best as a future external tool
that talks to ACC via the existing CLI rather than another TUI
screen.

### L4. Federation with non-ACC agent runtimes

**State:** ACC clusters are ACC-internal.

**Scope.**
* MCP-based external-cluster bridge: one ACC cluster member is
  a thin shim that delegates work to an external runtime
  (LangGraph, CrewAI) over MCP.
* External outputs round-tripped via the bridge into the cluster
  scratchpad.
* Cat-A enforcement at the bridge boundary.

**ROI.** ACC becomes the *governance overlay* for a mixed agent
fleet. Customers don't have to migrate; they wrap.

---

## Autoresearcher follow-ups (post PR #41-#46)

The autoresearcher demo (Example No. 2) shipped fully runnable but
surfaced its own follow-up list during implementation + the operator
review of revision 2 of the plan.

### A1. Live cost panel in the TUI

**State:** `tokens_used` is on the wire (TASK_COMPLETE), the cost
cap fires in the arbiter, ALERT_ESCALATE renders in Compliance —
but there is no per-plan cost rollup screen yet.

**Scope.**
* New panel on Performance (or a dedicated screen 8) that
  aggregates `plan_id` → `tokens_used` running total.
* `cost_remaining` rendered when `max_run_tokens > 0`.
* Sparkline of token spend over time per active plan.

**ROI.** Operators running production demos cite cost visibility
as the main reason they shy away from longer runs. ~250 LOC + 4
Pilot tests.

### A2. Active citation re-fetch verification

**State:** `acc/research/citation_verifier.py` confirms the
critic's audit trail (which URLs the critic re-fetched). It does
NOT itself re-fetch URLs to compare claim text.

**Scope.**
* New `acc/research/active_verifier.py` that takes the report +
  invocations + an LLM client; for each cited URL, fetches the
  page and asks the LLM whether the cited claim is supported.
* New `verify.sh --active` flag enables it; off by default
  because it adds substantial cost.
* Surfaces a per-claim verdict in the verification report.

**ROI.** Active defence against fabricated citations. Currently
the critic's own re-fetch is the source of truth; A2 makes the
verification doubly independent.

### A3. Cat-C promotion of useful prompt_patches

**State:** Patches survive A-021 sanity at apply time + persist in
the episode log; promotion to permanent `system_prompt.md` edits
is not wired.

**Scope.**
* Episode-log analyser that flags patches recurring across runs
  with above-threshold scores.
* Compliance-screen oversight item proposing the edit.
* Operator approves → arbiter rewrites the persona's
  `system_prompt.md` (versioned via existing role hot-reload).

**ROI.** Closes the self-improvement loop documented in revision 2
of the plan ("Cat-C promotion of useful prompt_patches" residual #4).

### A4. Iteration-quality experiment automated CI

**State:** Documented in `examples/acc_autoresearcher/README.md`;
not yet automated.

**Scope.**
* New CI job `acc-research-iter-quality.yml` that runs the demo
  at max_iterations=3 and =5 against a fixture topic, compares
  citation coverage + verdict scores, fails the build if 5
  iterations regress vs. 3.
* Prereq: a stable mock LLM backend so CI doesn't burn live cost.

**ROI.** Automated regression watchdog for the central operator
question "do more iterations actually help?".

### A5. Browser-harness lean variant

**State:** Reference container ships with full Chromium + LLM
client (~1.5-2 GB image).

**Scope.**
* Alternative `Containerfile.web_browser_harness.lean` —
  Firefox-only, no LLM-driven flow, ~600 MB.
* Operator opts via `BROWSER_HARNESS_LEAN=true`.

**ROI.** Edge / SNO deployments with constrained image storage.

### A6. Topic-slug derivation in the TUI

**State:** Operator passes `--topic <slug>` to `run.sh`. Operator
decision (revision 2) noted "TUI implementation/trigger on roadmap".

**Scope.**
* New prompt-pane action: "Submit autoresearcher plan…" opens a
  modal asking for a topic slug + cost cap; submits the plan
  + opens the cluster panel pre-populated.

**ROI.** Reduces the muscle memory required to demo the feature.

---

## Cross-cutting improvements

### C1. Telemetry — cluster cohorts

Group capability stats by cluster (in addition to today's per-skill
totals). Lets operators answer "which cluster shape costs most per
task?" without scrubbing the audit log.

### C2. Stochastic estimator (optional)

Difficulty bumps are deterministic. A small stochastic component
(e.g. ε-greedy: 5% of the time, +1 sub-agent over the heuristic
output) generates exploration data for L2 promotion.

### C3. Per-member step budget

Some members blow the role's `max_task_duration_ms` while siblings
complete. Extend the cancel path to time out individual members
without killing the whole cluster — fold the survivor outputs.

### C4. Prompt-pane PLAN previewer

Operators submitting a PLAN today need a separate terminal. Add a
modal that lets them paste / type a YAML PLAN, runs `lint`, and
submits via the existing observer publish path.

### C5. Markdown role authoring — UI

Reach for a role-editor pane in the TUI Ecosystem screen. Operators
edit `role.md` inline; lint runs on save; compile + reinfuse on
Apply. PR #28's CLI is the prerequisite — UI is sugar on top.

---

## What we explicitly will not do (yet)

* **Agent-to-agent direct subjects.** Members continue to talk
  through the bus + scratchpad. Adding per-pair channels balloons
  the wire surface and breaks the ACC-12 audit story.
* **Cluster-internal voting / consensus.** Aggregation is
  arbiter-side concatenation today. If a future workload truly
  needs Byzantine consensus inside a cluster, that's a separate
  capability with its own RFC — not a quiet addition to the
  estimator.
* **Hardcoded "smart" estimators in `acc/estimator.py`.** Operator
  estimators live in `module:` extensions. Built-ins stay simple
  and deterministic so production runs are reproducible.
* **A second TASK subject for cluster traffic.** All cluster
  signals stay on `acc.{cid}.task` / `acc.{cid}.task.progress` so
  one observer subscription captures everything. The `cluster_id`
  is the correlation key, not a routing prefix.

---

## Acceptance criteria for "clustering is feature-complete"

The five-PR series + the short-term items above gets us 80% of
what operators ask for. We declare clustering "feature complete"
when:

1. **Mixed-role clusters** (M1) ship and the demo plan can run with
   a single heterogeneous cluster instead of one cluster per
   persona.
2. **Agent-side cancel** (S1) lands and `/cluster kill` is
   operationally meaningful within seconds.
3. **Coding-agent persona library** (S2) lands and is documented
   in `acc/tui/help/prompt.md`.
4. **Cluster cohort telemetry** (C1) renders in Performance.
5. **Cat-C cluster promotion** (L2) ships at least the data
   collection (the rule synthesis can follow).

Past that point, clustering becomes part of the platform vocabulary
and feature work pivots to *workloads on top of it*: live coding
sessions, interactive code review, automated migration runs.
