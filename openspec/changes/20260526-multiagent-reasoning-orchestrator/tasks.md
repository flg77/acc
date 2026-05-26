# Tasks — multi-agent reasoning + orchestrator

## 2b — multi-agent reasoning surfacing
- [ ] Thread `agent_id` + `role` onto intermediate reasoning in TASK_PROGRESS
      (and per-PLAN-step / per-cluster-member / per-critic-iteration payloads);
      cap/truncate reasoning on the bus.
- [ ] Prompt screen: append a collapsible `reasoning` entry per agent_id across
      the task's whole fan-out (reuse the cluster/PLAN subscription), ordered
      chronologically with trace/progress lines.
- [ ] Reviewer/critic (PR-MM3): surface verdict deliberation as a
      "🧠 reviewer reasoning — <verdict>: <critique>" line.
- [ ] Tests: synthetic multi-agent TASK_PROGRESS/COMPLETE → one line per agent;
      reviewer critique line; Ctrl+R hides all.

## 2c — orchestrator/router role
- [ ] `roles/orchestrator/role.yaml` (reasoning_trace: true) emitting a
      structured routing decision (Options=candidate roles, Plan=dispatch).
- [ ] Routing path: orchestrator publishes a directed TASK_ASSIGN
      (target_role / cross-collective via ACC-9 [DELEGATE] + A-010); honours the
      PR-V4 role filter.
- [ ] Surface orchestrator routing reasoning as a first-class stream line.
- [ ] Pair with a strong model (multimodel registry) — document.
- [ ] Tests: orchestrator emits a routing decision; dispatched task handled by
      exactly the chosen role; governance still applies.
- [ ] Bench the routing reasoning via acc-reasoning-trace.

## Gate before promote
- [ ] pytest green; reasoning-bench gate (`python -m gate`) passes for the
      orchestrator's model; no regression.
