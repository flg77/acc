# 20261003-assistant-orchestrated-infusion — design

## 0. The flow this delivers

```
operator prompt
  └─ assistant: "this needs <role>"                    (today: works)
       ├─ role not installed?  PROPOSE_INFUSE           (today: works)
       ├─ no agent holds it?   PROPOSE_SPAWN + brief    (new: brief rides the spawn)
       │     └─ arbiter: free dormant worker?
       │          ├─ yes → signed ROLE_ASSIGN            (today: works, needs the key)
       │          └─ no  → PROPOSE_LIFECYCLE:scale       (new: broker adds a worker)
       ├─ role reports ACTIVE → route the parked brief  (new: no operator re-prompt)
       ├─ specialist works, with the ROLE's memory      (new: role-keyed memory)
       │     └─ TASK_COMPLETE {handover_id, result, notes}
       └─ assistant review turn                         (new)
             ├─ accept   → answer the operator, with the specialist credited
             ├─ refine   → route back with a critique   (≤ N rounds)
             └─ escalate → console decision
  idle specialist → pause, later stop               (new: PROPOSE_LIFECYCLE)
```

Every arrow publishes an outcome the Prompt pane renders, success or failure. The
silent-arbiter class of bug (`fix/reconcile-result-says-why`) is a design rule
here, not a patch: **no step may fail without telling the task that asked.**

## 1. Lifecycle authority: who may change containers, and how

Operator decision, 2026-10-03: the assistant may start, stop and pause containers to
infuse roles. The design question is how to grant that **without** handing a
model-driven agent the host.

### 1.1 Rejected: the container socket in the assistant

Mounting the podman socket (or a Kubernetes token with pod-create) into the
assistant gives it the host: any image, any mount, any privilege. A prompt
injection in a tool result would then be a container escape away from the
operator's machine. The assistant decides; it never executes.

### 1.2 Chosen: a lifecycle broker with a closed vocabulary

```
assistant ──PROPOSE_LIFECYCLE──▶ proposal pipeline (risk, operating mode, console)
                                     │ approved / AUTO
                                     ▼
arbiter ── signs LIFECYCLE_REQUEST (same Ed25519 key as ROLE_ASSIGN) ──▶ bus
                                     │
              ┌──────────────────────┴───────────────────────┐
              ▼                                              ▼
  podman: host lifecycle broker                  cluster: ACC operator
  (scripts/acc-lifecycle-watcher.sh,             patches AgentCollective
   generalised; subscribes to the bus            worker_pool / agents replicas;
   subject instead of polling a file)            RBAC: that CR only
```

The broker **verifies the arbiter signature** and then checks the request against a
closed vocabulary. It refuses anything else, and the refusal is published.

| Action | Target | Effect (podman) | Effect (cluster) |
|---|---|---|---|
| `scale` (alias `start`) | the pool | `podman start` the first **pre-created, stopped** `acc-worker-N` | raise `worker_pool` |
| `pause` | an idle specialist worker | `podman pause` (memory kept in RAM) | n/a on k8s: maps to stop |
| `resume` | a paused worker | `podman unpause` | n/a |
| `stop` | an idle specialist worker | `podman stop`, its role slot released | replicas →0 |

**As built in phase 2: `scale` never creates a container.** The pool is
synthesized ahead of time (`acc-deploy.sh apply`) and the spares are left
stopped; `scale` starts one. The cap is physically the pool's size, and no
request can introduce a container shape the operator did not deploy. Creating
workers at runtime would mean the broker running compose with request-derived
input, which is the open door the closed vocabulary exists to keep shut.

**Hard limits enforced by the broker, not by the model:**

- **Targets** are only containers labelled `acc.worker_pool=true` (or
  `acc.synthesized=true` with a specialist role). The control plane (`arbiter`,
  `assistant`, `compliance_officer`, `ingester`, `nats`, `acc-redis`, `acc-tui`,
  `acc-webgui`) is **never** a target, whatever the request says.
- **Images** are only the stack's own `acc-agent-core:<running tag>`. A request
  cannot name an image, a mount, an env var, a port or a privilege. Workers are
  synthesized by `acc.collective.roles_to_compose`, so their shape is the code's,
  not the request's.
- **Roles** must resolve in the role store (in-tree or an installed signed pack).
  Infusion stays the only way a new role arrives, and it keeps its signing floor.
- **Caps:** `ACC_LIFECYCLE_MAX_WORKERS` (default 6) and a rate limit (default 6
  actions per 10 minutes). A cap hit is an outcome, never a silent drop.
- **Mid-task:** `stop` and `pause` on a worker with a task in flight are refused
  unless the request carries `force`, and `force` is always a console decision.
- **Audit:** every request, verdict and effect goes to the tracelog and the audit
  chain (`prompt_record`-style evidence: who asked, which proposal, what changed).

### 1.3 Governance: the dispatch table

A new proposal kind, `PROPOSAL_LIFECYCLE`, with one row per action in the existing
`decide_dispatch` (mode × kind) contract, so changing a cell stays a deliberate,
tested governance change (`tests/test_assistant_proposal.py::test_full_dispatch_table`).

| Action | Risk | AUTO | ACCEPT_EDITS | ASK_PERMISSIONS | PLAN |
|---|---|---|---|---|---|
| `scale +n` (within cap) | MEDIUM | auto | queue | queue | plan |
| `start` / `resume` | MEDIUM | auto | auto | queue | plan |
| `pause` (idle) | LOW | auto | auto | queue | plan |
| `stop` (idle) | MEDIUM | auto | queue | queue | plan |
| any + `force` | HIGH | queue | queue | queue | plan |

"Queue" means the console decision panel (UX-01), with evidence: role, worker, idle
time, the task that asked, and the cap headroom. This is the "confirmed from the
console where necessary" half of the operator's expectation, and it is the mode,
not the model, that decides where "necessary" is.

### 1.4 Idle policy

The assistant proposes `pause` after `ACC_SPECIALIST_IDLE_PAUSE_S` (default 600 s)
without a task, and `stop` after `ACC_SPECIALIST_IDLE_STOP_S` (default 3600 s). These
are proposals like any other. In ASK_PERMISSIONS they queue, so an operator who
wants specialists kept warm says no once and snoozes the class (UX-05).

## 2. Spawn, then hand over

- **Marker:** `PROPOSE_SPAWN:<role>:<cluster>:<reason>` gains an optional
  follow-on line, `HANDOVER:<brief>`, or the assistant emits SPAWN and ROUTE in the
  same reply. The parser pairs a ROUTE to a role with a SPAWN of that role in the
  same reply.
- **Parking:** the assistant keeps `pending_handover[(role, cluster)] = {task_id,
  brief, proposal_id, deadline}` in Redis, so an assistant restart does not lose
  it.
- **Release:** on the arbiter's `reconcile_result` (assigned) **and** the worker's
  first ACTIVE heartbeat in that role, the assistant dispatches the parked route
  through the normal ROUTE path. That way the dispatch table and the oversight
  queue apply to the hand-off exactly as if the operator had typed "confirmed".
- **Failure:** `unmet` / `no_signing_key` / broker refusal / deadline
  (default 120 s) → the parked hand-off is dropped and the assistant tells the
  operator in the thread, naming the remedy. It never routes to a role no agent
  holds.

## 3. The review turn

- `_dispatch_route` already stamps `handover_id`. The specialist's TASK_COMPLETE
  echoes it (and `task_id`). The assistant subscribes to TASK_COMPLETE for the
  handover ids it issued.
- On arrival, the assistant runs a **follow-up task** built with the existing
  `follow_up_payload` mechanism (MC-03): the original request, its brief and the
  specialist's result. That way the specialist's output meets the same `pre_llm`
  guardrails as any untrusted input. A review turn is a tool result, not a system
  message.
- The assistant answers with one of three markers:
  - `[REVIEW:accept]`: the reply to the operator is the reviewed answer.
  - `[REVIEW:refine:<critique>]`: re-route to the same role with the critique.
    Bounded by `ACC_REVIEW_MAX_ROUNDS` (default 2); round N+1 is an escalation.
  - `[REVIEW:escalate:<why>]`: a console decision with both texts as evidence.
- **Notes to the assistant:** the specialist's result may carry
  `notes_for_assistant` (what it learned, what it could not check). Those go into
  the review turn, and through the existing lessons path (`acc.<cid>.knowledge.*`)
  into the assistant's memory under the specialist's attribution.

## 4. Memory that belongs to the role

- Today `_promote_from_dormant` reuses the worker's `LanceDBBackend` at
  `ACC_LANCEDB_PATH=/app/data/lancedb/worker-N`.
- Change: on promotion, re-open the vector backend at
  `/app/data/lancedb/roles/<role>[--<cluster>]` (same volume, which every worker
  already mounts). On demotion or stop, close it. Two live workers in the same role
  get the same path; LanceDB tolerates concurrent writers per table, and this is
  written up as a constraint in tasks §4, not assumed.
- Memory scope (`acc/memory_scope.py`) keys stay as they are: role memory is still
  scoped by requester, so moving it to a role path does not widen who reads what.

## 5. Gaps this design depends on, recorded here

- **Workers have no `/workspace` mount.** `roles_to_compose` omits it, so a promoted
  specialist cannot `fs_write` its report. Add `/workspace` (and the read-only
  `/workspace/docs`) to synthesized workers.
- **"The local server" is not the container.** A `devops_engineer` running
  `shell_exec` in a worker inspects the worker, not the host. A host security
  audit needs an explicit, read-only host view: the TUI's `/host-fs` read-only
  mount, or `ssh_exec` to the host with a scoped key. That choice changes the
  role's risk ceiling, so it is an operator decision. **Open question 1.**
- **Where the broker runs.** See §5a: a compose service with the podman API
  socket, or a host process. **Open question 3.**
- **Signing key on a single host.** The doc permits both arbiter keys in `.env`
  for a standalone demo. With lifecycle authority added, the signing key is worth
  more. Recommend an arbiter-only mount (`acc-secrets` volume, `:ro`, arbiter
  service only). **Open question 2.**

## 5a. Phase 2 as built

- `acc/lifecycle.py`: the pure core. It holds the vocabulary, the signed request
  (Ed25519, the arbiter's ROLE_ASSIGN key; every field is signed, the age limit
  is 120 s, there is a replay guard), the target rule, `plan()` per action,
  the rate limiter, and the `lifecycle_result` outcome.
- `acc/lifecycle_broker.py`: NATS in (`lifecycle.request`, heartbeats), the
  libpod REST API out (`start | stop | pause | unpause` only), and an outcome
  for every request.
- The arbiter subscribes to `lifecycle.intent`, refuses unknown actions,
  unresolvable roles and a missing signing key **on the task**, and signs the
  rest. When a `scale` reports done, it re-runs the reconcile, so the waiting
  spawn lands on the new worker.
- The NATS matrix gains a `lifecycle_broker` identity: publish `assistant.*` and
  `alert`; subscribe `lifecycle.request` and `heartbeat`. The arbiter may publish
  `lifecycle.intent` and `lifecycle.request`. The operator does not mint the
  identity, because on a cluster the operator is the broker (phase 6).
- Heartbeats carry `tasks_in_flight` and `last_task_done_ts`, used for the busy
  guard and the idle policy.
- Synthesized workers mount `/workspace` and `/workspace/docs:ro`. The
  synthesize CLI's default image follows `ACC_VERSION`.
- **Not in phase 2: how the broker is deployed.** It needs the rootless podman
  API socket. A compose service with the socket mounted puts host-level
  container control inside a container; a host process needs Python ≥ 3.12 on
  the host. That is an operator decision (**open question 3**), and nothing in
  the repo wires either yet.
- **Not in phase 2: the idle policy** (pause at 600 s, stop at 3600 s). The
  heartbeat fields it needs are in place.

## 6. Phases

1. **Visibility.** Shipped separately: the arbiter reports refusals.
2. **Lifecycle broker (podman)**, plus `PROPOSE_LIFECYCLE` (scale / start / stop /
   pause / resume), the dispatch-table rows, caps and audit. Workers get `/workspace`.
3. **Spawn-then-hand-over** (parking, release on ACTIVE, deadline).
4. **Review turn** (`REVIEW:*`, bounded refine, notes to the assistant).
5. **Role-keyed memory.**
6. **Cluster parity:** operator path for `PROPOSE_LIFECYCLE` (AgentCollective
   patch, RBAC to that CR).

Each phase is independently shippable and keeps the full suite green. The
experiment's after-measurement is taken at the end of phase 4. Phase 5 is measured
separately: a role keeps a fact across stop → start.

## 7. Threat notes

New attack surface, each with its control:

| Threat | Control |
|---|---|
| Prompt injection makes the assistant spawn or scale endlessly | broker caps + rate limit; AUTO limited to MEDIUM; every action audited and visible |
| A forged lifecycle request on the bus | broker verifies the arbiter's Ed25519 signature; unsigned or mis-signed requests are refused and published |
| The assistant stops the control plane or the compliance officer | the broker's target allow-list excludes the control plane by label and by name; not configurable by request |
| A request smuggles an image, mount or privilege | the vocabulary has no such fields; workers are synthesized by code |
| A specialist's result injects instructions into the assistant | the review turn goes through `follow_up_payload` → `pre_llm` guardrails, as tool output does today |
| Stopping a worker mid-task loses work | `force` required and always a console decision |
