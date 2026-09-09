# Architecture

## Overview

ACC (Agentic Cell Corpus) is a governed collective of LLM agents ("cells") that
coordinate over a NATS bus, keep shared working state in Redis and durable
memory in a vector store, and are driven by three operator surfaces — CLI, TUI
and Web GUI — that never act on their own authority: every mutation that
matters is a signal the arbiter or an agent applies under Cat-A/B/C governance,
with a human oversight queue for what a role's grants do not cover.

## Components

| Component | Role |
|---|---|
| `acc/agent.py` (one process per cell) | `CognitiveCore` task loop: gate → retrieve memory → build prompt → LLM → post-gate → persist episode → drift. Subscribes to the collective's subjects; applies `OVERSIGHT_DECISION`s; dispatches assistant proposals under an atomic claim. |
| **arbiter** (a cell with the arbiter role) | Signs `ROLE_UPDATE`s (Ed25519), runs the `PlanExecutor` (PLAN DAGs, `PLAN_STEP_CONTROL`), the worker-pool reconcile, cluster fan-out; its HEARTBEAT carries the oversight queue and the decision history. |
| **hub_curator** (role, v0.14.1) | The enterprise brain's only agent: no chat surface, no skills; on its schedule it proposes qualifying shared notes from the instances bound to its hub into the hub's enterprise tier; a person approves at operator tier. |
| **assistant** (role) | The operator's router: proposes infusion of catalog roles, specialist hand-off (spawn/route), never learns across people without a publish proposal (D-011, memory Phases 4–6). |
| `acc/oversight.py` `HumanOversightQueue` | Pending / decided items in Redis (shared by every cell). A decision is final; one row per decision; one row per submit (D-013). |
| `acc/capability_dispatch.py` + `acc/operating_modes.py` | Skill / MCP invocation behind the role's `allowed_*`, gate categories (system access, acts on behalf), escalation for an off-role grant, operating modes `AUTO / PLAN / ACCEPT_EDITS / ASK_PERMISSIONS`. The requester's **category ceiling** (`acc/identity.py`, D-014) is checked first: above it, refused, never asked. |
| `acc/backends/` | Pluggable LLM (vLLM, Ollama, Anthropic, OpenAI-compatible, with a failover chain), vector (LanceDB / Milvus / TurboVec), signaling (NATS), metrics (OTel / MLflow). |
| Memory (`acc/memory_*`, `acc/attribution.py`, `acc/memory_scope.py`, `acc/memory_curate.py`) | Episodes per requester + scope, private / shared note tiers, publish proposals with a quorum of people, erasure. A **hub's enterprise tier** (D-016) is the only cross-instance read path; every note carries the highest ceiling of its sources and is never read below it. |
| `acc/pkg/` | `.accpkg` packages: cosign-verified install (keyless bundle or key), catalogs (built-in day-0, https, local), AgentBOM. |
| `acc/tui/` (Textual) | Thirteen screens declared in one registry (`acc/tui/registry.py`), two profiles (`operator`, `user`), the `NATSObserver` that folds every signal into a `CollectiveSnapshot`, the Prompt pane's `PermissionRequest`, the Board. |
| `acc/webgui/` (FastAPI + React) | The same snapshot over a WebSocket — each socket receives its principal's view (D-017) — attributed actions and prompts (`webgui:<user>`), config surface, Board, the OpenAI-compatible endpoint. |
| `acc/instances.py` + `acc/cli/instance_cmd.py` | An **instance** (D-015): a collective bound to an owner, a posture and its own state roots under `instances/<id>/`; `acc-cli instance …`, `./acc-deploy.sh instance up`; export carries the definition, never state. |
| `acc/cli/` (`acc-cli`, `acc-pkg`) | Headless operator surface: doctor, status, config, profiles, sessions, oversight, plan, memory, access, auth, egress, backup, scan… (see `CAPABILITIES.md`). |
| `operator/` (Go) | The OpenShift/Kubernetes operator: `AgentCorpus` / collectives as CRDs, role sync, SPIFFE, NetworkPolicy, pack installs, the console plugin. |

## Data flow

- **Subjects.** Everything rides on `acc.<collective_id>.*`: `task.assign` /
  `task.progress` / `task.complete` / `task.cancel`, `plan.submit` /
  `plan.control`, `heartbeat`, `register`, `role_update`, `oversight.<id>` /
  `oversight.submit`, `assistant.proposal`, `collective.reconcile`.
- **Wire format (D-012).** One format: `msgpack(json.dumps(payload).encode())`,
  enforced once in `NATSBackend.publish`; consumers decode `unpackb → json.loads`
  and tolerate a bare map from an older agent.
- **A prompt.** A surface publishes `TASK_ASSIGN` (`target_role`, `operating_mode`,
  `requester`, optional `session_id`); the agent that owns the role runs the
  cognitive core; `TASK_PROGRESS` streams steps; `TASK_COMPLETE` carries the
  reply, invocations and outcome; a task whose LLM call failed still ends,
  as a blocked completion with `task_error` (v0.12.1). Continuity replays
  earlier turns from the durable tracelog, never from a client transcript
  (RP-02).
- **A gate.** Anything beyond the role's grants — a system-access skill, acting
  on behalf, an off-role tool, a proposal in `ASK_PERMISSIONS` — submits an
  oversight item (unless it is above the requester's ceiling, which refuses
  it without asking); the arbiter HEARTBEAT carries the pending list; a human
  answers in the Prompt pane or Compliance (or `acc-cli`), which publishes
  `OVERSIGHT_DECISION`; every agent applies it, one claims the dispatch.
  Auto-executed proposals under `AUTO` are recorded as `AUTO_APPROVED` rows.
- **A plan.** `PLAN` → the arbiter's executor dispatches steps as tasks in
  dependency order (fan-out clusters where the estimator says so), each step
  carrying the plan's attribution — requester, tier, ceiling, scope — so it
  runs as the person who submitted the plan (v0.14.3), re-broadcasts
  the PLAN with `step_progress` / `step_tasks` on every transition, records
  each transition in the tracelog (`KIND_PLAN_STEP`), mirrors the body to Redis
  for a day and summarises active plans in its HEARTBEAT (a late observer
  cold-starts from it); the Board is a pure projection of that plus the pending
  gates, with fanned-out members folded under their step; `PLAN_STEP_CONTROL`
  (cancel / retry / reassign) is applied by the executor.
- **Evidence.** Every LLM call's corpus is digested into the HMAC-chained audit
  record (DS-01); the tracelog holds prompts, tool calls, governance verdicts,
  oversight rows and plan steps' tasks; `acc-cli sessions verify` replays it.

## Infrastructure

- **Standalone / edge**: `acc-deploy.sh` + podman-compose — NATS, Redis, one
  container per cell (`acc-agent-core`), `acc-tui`, optional `acc-webgui`
  (`up --webgui`), MCP servers; images tagged by `git describe`. An
  **instance** runs beside the base stack from its own overlay
  (`instance up <id>`): its cells share NATS, Redis and the pack registry and
  nothing else; under rootless podman they own their `lancedb/` and `trace/`
  roots (`U` mounts). Lighthouse
  (RHEL 10, RHAIIS vLLM, 3B FP8) is the reference edge box.
- **OpenShift / RHOAI**: the Go operator reconciles collectives from CRDs, with
  SPIFFE identity, NKeys on the bus, NetworkPolicy, pack installs into agent
  pods, oauth2-proxy/Keycloak in front of the Web GUI, the console plugin.
- **Code execution**: OpenShell Model 2 — agents stay rich pods and delegate
  `shell/python/ssh_exec` to gateway-created sandboxes (`acc/sandbox/`); fails
  closed when the gateway is unreachable.
- **Release flow**: spearhead leads (`flg77/acc-spearhead`), the public mirror
  (`flg77/acc`) receives a **curated squash** per release minus the four
  threat-model files; mirror tags differ from spearhead tags.

## Testing strategy

- **Unit / integration (pytest, ~5.2k)**: every module; TUI screens through
  Textual's pilot with a fake observer; WebGUI through `TestClient`; the wire
  format pinned at both ends; five pre-existing workstation reds (cosign env,
  model registry) are classified against `main` before treating anything as a
  regression.
- **Generated catalogs**: `docs/tool-catalog.md` (every model-facing tool) has a
  drift test; `openspec/changes/*` carry the task ledgers.
- **Live host evidence**: for every release, the staged suites on lighthouse
  (`git archive` → venv → pytest), then a live smoke — a 3-step PLAN across both
  surfaces, and the Prompt-pane approve / reject / allow-once smoke driving the
  **real `ACCTUIApp`** headlessly against the live bus (`approve_pilot.py`), with
  a bus sniffer for wire checks. Evidence docs live in the operator's vault
  (`ACC-Tests/`).
- **Reasoning bench**: the promote gate scores deliberation depth; run for any
  reasoning-affecting change (role prompts), advisory on the 3B edge model.

_Last updated: 2026-09-07 (v0.14.3)_
