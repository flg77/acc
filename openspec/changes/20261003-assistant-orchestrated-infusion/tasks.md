# 20261003-assistant-orchestrated-infusion — tasks

## 0. Measurement (before anything lands)
- [x] Baseline from the live failure: session `20261003-212631-1`, recorded in `proposal.md`
- [ ] Golden set: five prompts that each need a specialist the base stack does not run (e.g. host security report → `devops_engineer`; a contract clause review → `contract_analyst`; a market-size estimate → `macro_strategist`; a literature synthesis → `research_synthesizer`; a packaging/CVE check → `packaging_engineer`)
- [ ] Baseline run of all five (expected 0/5); record per prompt: specialist chosen, worker started, re-prompts, hand-off delivered, result back, reviewed answer
- [ ] One `acc-reasoning-trace` row for the assistant's routing turn (regression guard only)

## 1. Visibility (shipped separately)
- [x] Arbiter publishes `reconcile_result{reason: no_signing_key}` instead of returning silently — branch `fix/reconcile-result-says-why`
- [x] Spawn trigger carries `task_id`; the console and work board name the missing key

## 2. Lifecycle broker + `PROPOSE_LIFECYCLE` (podman)
- [x] `acc/lifecycle.py`: request schema (action ∈ scale|stop|pause|resume, `start` = scale; role, cluster, proposal_id, task_id), Ed25519 sign/verify with the arbiter key, 120 s age limit, replay guard
- [x] Marker `[PROPOSE_LIFECYCLE:<action>:<role>:<reason>]`, placeholder + fence rules as for the other markers; documented in the assistant role
- [x] `decide_dispatch` rows per design §1.3 (one kind per action); `test_full_dispatch_table` updated on purpose
- [x] Broker `acc/lifecycle_broker.py` (Python, not the shell watcher: it has to verify Ed25519): subscribes `lifecycle.request` + heartbeats; verifies; target rule by label, name prefix and control-plane deny list; `scale` only starts pre-created workers (cap = pool size); rate limit; publishes `lifecycle_result`
- [x] Arbiter: `lifecycle.intent` → validate → sign → `lifecycle.request`; refusals published on the task; reconcile after a successful scale
- [x] NATS matrix: `lifecycle_broker` identity; arbiter may publish `lifecycle.intent` / `lifecycle.request`
- [ ] **Deploy the broker** (OQ3): compose service with the rootless podman API socket vs a host process — operator decision; not wired
- [x] Mid-task guard: stop/pause refused on a worker with `tasks_in_flight > 0` (heartbeat field added)
- [ ] `force` (stop a busy worker) as a HIGH, always-queued proposal — deferred
- [ ] Audit: request, verdict, effect on the tracelog and audit chain (outcomes are published and logged; audit-chain record not yet)
- [x] `roles_to_compose`: workers get `/workspace` and `/workspace/docs:ro`; the synthesize CLI's default image interpolates `ACC_VERSION` instead of the `0.2.0` literal
- [ ] `apply` without host Python: run `collective synthesize` in the `acc-cli` image when the host lacks 3.12 (found 2026-10-03)
- [ ] Idle policy proposals (pause at 600 s, stop at 3600 s)
- [x] Tests: forged / unsigned / tampered / stale / replayed / wrong-collective refused; control-plane target refused; pool exhausted, rate limit, busy worker, podman failure reported; each mode × action cell; arbiter signs / refuses (`tests/test_lifecycle.py`, `tests/test_lifecycle_broker.py`)

## 3. Spawn, then hand over
- [x] SPAWN + ROUTE of the same role in one reply are paired (`acc.handover.pair_spawn_and_route`); the route keeps its execute-or-queue classification. (A separate `HANDOVER:` line was not needed: the assistant role now emits both markers together.)
- [x] Parked hand-offs in Redis (`acc:<cid>:pending_handover:<route id>`, TTL past the deadline), memory fallback; restored on assistant start. Keyed per route, matched on role: a promoted worker's heartbeat carries no cluster id.
- [x] Released on an ACTIVE heartbeat in the role, or a reconcile reporting the role already active, through the route's own execute-or-queue path (oversight applies). Not on `assigned` alone: a worker only hears role-targeted tasks once promoted.
- [x] No dormant worker → one `PROPOSE_LIFECYCLE:scale` through the dispatch table, keep waiting; no signing key / refused scale / deadline (120 s, 1800 s when the spawn waits for the console) → drop; every step publishes `handover_parked | waiting | released | dropped`, rendered in the Prompt pane and the work board
- [x] Tests (`tests/test_handover.py`): pairing; release by heartbeat / already-active; dormant and other-role heartbeats ignored; scale asked once; no key, refused scale, deadline drop; restart restore; release executes or queues per classification; a reply that spawns and routes dispatches only the spawn

## 4. Review turn
- [x] **Found and fixed first:** a route carried no content field, so the receiver dropped it as empty (`_task_has_content`) — no hand-off had ever been worked on. The route now carries the brief + original request (+ critique) as `content` and a `handover_block` (id, origin agent, role, task, round, goal, brief, mode)
- [x] Specialist TASK_COMPLETE echoes `handover_block`, its `role` and `notes_for_assistant` (`[NOTE_FOR_ASSISTANT: …]` lines, ≤5 × 500 chars)
- [x] Assistant reviews completions whose block names it (stateless: works whether it or the arbiter dispatched the route); the review is a local task through the normal task path, so pre-LLM guardrails see the answer, framed as untrusted between markers. (Built as its own task, not `follow_up_payload`: the original payload is not available when the answer arrives.) Same `task_id`, so the reviewed reply lands in the operator's thread.
- [x] Markers `[REVIEW:accept]`, `[REVIEW:refine:<critique>]`, `[REVIEW:escalate:<why>]` (last outside fences wins; none = accept); `ACC_REVIEW_MAX_ROUNDS` = 2; refine re-routes through the dispatch table; past the limit or with no critique → escalation to the oversight queue with evidence (request, brief, why, the assistant's read)
- [x] Notes → a `Lesson` with `target_agent_id` = the assistant that asked, from the specialist (agent + role), on the existing lessons channel
- [x] Tests (`tests/test_review.py`): route carries content + block; refine carries critique + round; notes bounded; untrusted framing; verdict parsing (last wins, fences ignored); bounds; only our hand-offs reviewed; accept / refine / past-limit escalate / empty-critique escalate; notes targeted at the assistant
- [ ] A live check that `pre_llm` flags an injection inside a specialist answer (the answer goes through the same path as any prompt; not separately tested here)

## 5. Role-keyed memory
- [x] `_promote_from_dormant` opens the vector backend at `/app/data/lancedb/roles/<role>[--<cluster>]` (cluster `default`/empty shares the role's store; names sanitised) before the core is built; `ACC_ROLE_MEMORY=0` turns it off; a failure to open keeps the worker's own store instead of failing the promotion. LanceDB only: Milvus / turbovec keep per-worker memory (logged). A stopped worker holds no handle, so there is nothing to close on stop.
- [x] **Found:** episode retrieval also filtered on `agent_id` ("only what THIS agent did"), so moving the store alone would still hide worker-1's episodes from worker-2. A core on a role store sets `_shared_role_memory` and skips only that check; memory notes were already keyed by role in Redis.
- [x] Verified on LanceDB 0.39 (agent-core image), separate processes (`spawn`; separate containers in production): 2 writers × 100 appends to one table → 200/200 rows, 0 conflicts; 5 rounds of two `LanceDBBackend` boots racing table creation on a fresh path + 2 × 30 inserts → 60/60 every round, 0 errors. (A `fork`-based run deadlocks LanceDB's runtime: a test-harness hazard, not a production one.)
- [x] Memory scope unchanged: `row_scope(row) != scope` still filters; test shows a sibling's `local` row is read, a `user:alice` row is not, and is read only under alice's scope
- [x] Test: worker-1 learns a fact as `devops_engineer`; worker-2 promoted to the same role sees it; an `analyst` worker does not (`tests/test_role_memory.py`). Live measurement still to do with a real pool.

## 6. Cluster parity
- [x] Same broker, a Kubernetes runtime (`acc.lifecycle_broker.KubernetesRuntime`, `ACC_LIFECYCLE_RUNTIME=kubernetes`): JSON Patch on `spec.agents[i].replicas` of its own AgentCollective (a `test` op guards concurrent edits; one re-plan on 409/422); `acc.lifecycle.plan_cluster` decides: scale = +1 on a declared role (adding a role stays an operator edit), stop = 0 (refused while any pod of the role is busy), per-role cap, control roles (arbiter, assistant, compliance_officer) never touched, KEDA-scaled collectives refused
- [x] Operator: `spec.lifecycle {enabled, verifyKey (SecretKeySelector), maxReplicas}` on AgentCollective; the collective reconciler runs `<collective>-lifecycle-broker` (Deployment + ServiceAccount + Role + RoleBinding) whose Role is `get, patch` on `agentcollectives` with `resourceNames: [<collective>]` — nothing else; disabling deletes all four (the power, not just the pod). The operator's ClusterRole already holds every verb it grants.
- [x] NKey: `lifecycle_broker` added to the operator's identity list; an existing `<corpus>-nats-nkeys` Secret is topped up append-only with missing seeds (existing seeds never rewritten); the broker pod gets only `seed-lifecycle_broker`
- [x] Deepcopy + CRD regenerated with controller-gen v0.16.1 (the repo's pinned version, built on Go 1.22 because it does not compile on 1.25); only `zz_generated.deepcopy.go` and the AgentCollective CRD changed (+ its byte-identical bundle copy); `make manifests` deliberately not run (it regenerates `config/rbac` from markers over the hand-maintained ClusterRole)
- [x] `pause` = replicas 0 with the count kept in the `acc.redhat.io/paused-replicas` annotation; `resume` restores it (outcomes name the replica change, e.g. `devops_engineer replicas 2->0`)
- [ ] Live: an AgentCollective with `spec.lifecycle.enabled` on acc1 / bb3 (operator image build + rollout)

## 7. Open questions (operator)
- [ ] OQ1: host view for host-inspection roles: read-only `/host-fs` mount vs `ssh_exec` to the host with a scoped key (changes the role's risk ceiling)
- [ ] OQ2: arbiter signing key as an arbiter-only mount (`acc-secrets`, `:ro`) instead of the shared `.env`
- [ ] OQ3: where the lifecycle broker runs — a compose service with the rootless podman API socket mounted, or a host process (needs Python ≥ 3.12 on the host)

## 8. After-measurement + outcome
- [ ] Re-run the five golden prompts after phase 4; record per prompt; target 5/5 with zero operator re-prompts in AUTO
- [ ] ASK_PERMISSIONS run: every lifecycle action and hand-off waited for a console decision
- [ ] Full suite green; outcome (kept / reverted / needs iteration) in `proposal.md`
