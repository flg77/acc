# Proposal: Multi-agent reasoning stream + cross-collective orchestrator

| Field      | Value                                                       |
|------------|-------------------------------------------------------------|
| Change ID  | 20260526-multiagent-reasoning-orchestrator                  |
| Date       | 2026-05-26                                                  |
| Status     | Draft                                                       |
| Branch     | `feat/ACC-multiagent-reasoning-orchestrator`                |
| Depends on | PR-V3b (reasoning_trace), PR-V4 (target_role routing + collapsible reasoning UI), PR-MM3 (reviewer/critic), ACC-9 (cross-collective bridge) |

---

## Problem statement

Two follow-ups from the ACC-Reasoning debug session (items 2b/2c). PR-V3b/V4
landed single-agent reasoning: a role-targeted prompt now goes to exactly that
role, whose deliberation shows as a collapsible one-liner (Ctrl+O). But:

1. **Multi-agent context (2b).** When a task fans out across several agents
   (a PLAN/DAG, a sub-agent cluster, or a reviewer critic loop), only the final
   reply's reasoning surfaces. The operator needs **every participating agent's
   reasoning**, each as its own collapsible one-liner in the stream, so the
   whole collective's deliberation is visible.

2. **Orchestration (2c).** There is no agent that *reasons about routing*. In
   cross-collective / cross-"border" scenarios (ACC-9 bridge delegation) the
   operator asked: "how did the orchestrator conclude it should hand the job to
   coding_agent?" Today that decision is implicit (operator picks the role, or
   the arbiter routes mechanically). We need an **orchestrator/router role**
   that, given a task, deliberates over which role/collective should handle it
   and **surfaces that decision** ("→ coding_agent because …") in the same
   reasoning stream.

A global toggle (Ctrl+R, already added in PR-V4) hides the stream when the
operator feels overwhelmed; default on.

## Proposed approach

### 2b — multi-agent reasoning surfacing
- Every agent already emits `reasoning` on `TASK_COMPLETE` (PR-V3b) and
  `TASK_PROGRESS` at step boundaries (PR-V3). Extend the bus payloads so
  **intermediate** reasoning (per PLAN step / per cluster member / per critic
  iteration) carries the emitting `agent_id` + `role`.
- The Prompt screen subscribes to the task's *whole* fan-out (it already gets
  cluster/PLAN signals) and appends a `reasoning` transcript entry **per agent**
  keyed by agent_id, each collapsible. Order chronologically with the existing
  trace/progress lines.
- The reviewer/critic loop (PR-MM3) surfaces its verdict deliberation as a
  "🧠 reviewer reasoning — <verdict>: <critique>" line instead of only folding
  it into `eval_outcome`.

### 2c — orchestrator/router role
- New `roles/orchestrator/role.yaml` (`reasoning_trace: true`): given a task it
  emits a structured routing decision — chosen role/collective + rationale —
  using the reasoning block (Options = candidate roles, Evaluation = fit, Plan =
  the dispatch).
- A routing path: the orchestrator publishes a directed `TASK_ASSIGN`
  (`target_role`/`target_collective`) honouring the PR-V4 role filter and, for
  cross-collective, the ACC-9 `[DELEGATE:...]` bridge + A-010 gate. Its routing
  reasoning rides to the Prompt stream as a first-class "🧠 orchestrator
  reasoning — → coding_agent because …" line.
- Opt-in: a collective wires an orchestrator only when it wants autonomous
  routing; direct operator prompts keep going straight to the chosen role.

## Out of scope
- Changing the deterministic direct-prompt path (PR-V4 stays the default).
- A graphical DAG of reasoning (the stream is line-based, collapsible).

## Risks
- Bus volume: per-step per-agent reasoning multiplies TASK_PROGRESS payloads —
  cap/truncate reasoning on the bus (full text stays in the episode), and the
  Ctrl+R toggle hides the stream.
- Orchestrator quality is model-bound (see acc-dev-harness finding 001: small
  models reason inconsistently) — pair the orchestrator with a strong model via
  the multimodel registry.
- An orchestrator that mis-routes must not bypass governance — every dispatched
  task still passes the target agent's Cat-A/B gates.

## Verification
- Multi-agent: a PLAN/cluster task shows one collapsible reasoning line per
  participating agent, chronologically; reviewer critique appears as its own
  line. Pilot test feeding synthetic multi-agent TASK_PROGRESS/COMPLETE.
- Orchestrator: a task to the orchestrator role emits a routing decision whose
  reasoning names the chosen role + rationale; the dispatched task is handled by
  exactly that role (PR-V4 filter); cross-collective uses the ACC-9 bridge.
- Bench the orchestrator routing reasoning via acc-reasoning-trace.
