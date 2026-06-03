# HOWTO — Agentsets with `collective.yaml`: spawn, swap, and collaborate

`collective.yaml` is the **declarative agentset** for the standalone Podman
stack: it says *which roles run, how many, on which model, in which cluster*.
`./acc-deploy.sh apply <file>` synthesizes a podman-compose overlay from it and
brings those agents up **alongside** the always-on baseline — so you can stand
up a purpose-built team of agents, point them at a problem, then swap the whole
team for a different one without touching the base stack.

This guide shows how to author role-specific agentsets, spawn them, swap between
them, and — the point of it all — wire teams that **work a problem together**.

> Schema mirrors the K8s operator's `AgentCollectiveSpec`
> (`operator/api/v1alpha1/agentcollective_types.go`), so an agentset you design
> here lifts to RHOAI later. Per-agent models come from `models.yaml`
> (see [`docs/multimodel_reviewer.md`](multimodel_reviewer.md) for endpoints +
> API keys).

---

## 1. The mental model: baseline + agentset overlay

| Layer | Defined in | Services | Lifecycle |
|---|---|---|---|
| **Baseline** | `container/production/podman-compose.yml` | `acc-nats`, `acc-redis`, `acc-tui`, the core `acc-agent-{ingester,analyst,arbiter}` | `./acc-deploy.sh up` |
| **Agentset** | `collective.yaml` (or a preset) | `acc-cell-<prefix>-<n>` | `./acc-deploy.sh apply <file>` |

The two **coexist cleanly** — agentset services are named `acc-cell-*`, distinct
from baseline `acc-agent-*`, and share the same NATS bus + Redis + LanceDB
(episodic memory) so every agent is part of one collective (`collective_id`).
The default `collective.yaml` ships `agents: []` — the baseline is enough until
you add a team.

---

## 2. The schema

```yaml
collective_id: sol-01          # the bus namespace all agents share

agents:                        # the EXTRA agents (the agentset)
  - role: coding_agent_implementer   # a role under roles/<id>/role.yaml
    replicas: 2                       # how many of this agent (0–100)
    cluster_id: backend               # operator tag; PLAN/fan-in group them
    purpose: "Implement decomposed coding tasks."   # human note
    model: claude-haiku               # a model_id from models.yaml (optional)
    agent_id_prefix: impl             # service/agent-id prefix (optional)

worker_pool: 0                 # dormant slots the arbiter fills via ROLE_ASSIGN
heartbeat_interval_seconds: 30 # optional override
```

Each entry produces `replicas` services named `acc-cell-<prefix-or-role>-<n>`.
Omit `model` and the agent uses the collective default (`acc-config.yaml` /
`.env`). A keyed model needs its `api_key_env` value in `.env`.

---

## 3. Lifecycle — spawn, inspect, swap, tear down

```bash
# Preview what an agentset would create (no changes):
./acc-deploy.sh apply --dry-run collective.coding-split.yaml

# Spawn it (adds the agents; baseline + memory untouched):
./acc-deploy.sh apply collective.coding-split.yaml

# See the synthesized agents:
podman ps --filter label=acc.synthesized=true

# SWAP to a different agentset — re-apply another spec.  apply runs with
# --remove-orphans, so the previous agentset's agents are removed and the new
# one's come up; the baseline stays put.  (Agent memory in the shared LanceDB /
# Redis / NATS volumes survives the swap.)
./acc-deploy.sh apply collective.autoresearcher.yaml

# Edit collective.yaml by hand OR via the TUI (Ecosystem → Agentset tab:
# add agents, pick a Model from the dropdown, Save → Apply), then re-apply.

# Tear the whole stack down (baseline + agentset):
./acc-deploy.sh down
```

> **Heads-up:** `apply` uses `--remove-orphans`. Applying a *broken* spec that
> fails to merge can remove agents before erroring — always `--dry-run` first
> on an unfamiliar spec, and keep the baseline recoverable with `up`.

---

## 4. Composing a role-specific dedicated agentset

A dedicated agentset = pick the roles for the job, size them, assign models,
group them with `cluster_id`. Example — a focused "backend coding cell":

```yaml
collective_id: sol-01
agents:
  - role: coding_agent_architect    # decompose the task
    replicas: 1
    cluster_id: backend
    model: claude-sonnet            # the planner gets the strong model
    agent_id_prefix: arch
  - role: coding_agent_implementer  # do the work
    replicas: 3
    cluster_id: backend
    model: claude-haiku             # cheap, parallel workers
    agent_id_prefix: impl
  - role: coding_agent_tester
    replicas: 1
    cluster_id: backend
    model: claude-haiku
    agent_id_prefix: test
  - role: reviewer                  # critique + advise revisions
    replicas: 1
    cluster_id: backend
    model: claude-sonnet            # strong reviewer lifts cheap workers
    agent_id_prefix: review
```

Design rules of thumb:
- **One `cluster_id` per team** — clusters are how the arbiter's `PlanExecutor`
  fans a task out and aggregates results, and how the TUI groups them.
- **Mixed models** — cheap workers + a strong planner/reviewer is the high-ROI
  pattern (see `collective.reviewer.yaml`).
- **Single-instance the reviewer/orchestrator** — multiple fragment the verdict.
- The role must exist under `roles/<id>/role.yaml` (`acc-tui` Ecosystem lists
  them; author new ones per [`docs/role-authoring.md`](role-authoring.md)).

---

## 5. The shipped presets

| Preset | Team | Use for |
|---|---|---|
| `collective.coding-split.yaml` | 3 `coding_agent`s in cluster `backend` | parallel coding |
| `collective.reviewer.yaml` | cheap implementer/tester workers + 1 strong `reviewer` | quality via the critic loop |
| `collective.autoresearcher.yaml` | 6 research roles (planner / economist / competitor / strategist / synthesizer / critic) | multi-perspective research → report |
| `collective.orchestrator.yaml` | `orchestrator` router + `coding_agent` + `analyst` workers | route mixed tasks to the right role |
| `collective.worker-pool.yaml` | dormant `worker_pool` slots | arbiter-activated elastic capacity |

Apply any with `./acc-deploy.sh apply <preset>`.

---

## 6. How agentsets work *together* — the collaboration surfaces

Agents in one collective interact over the NATS bus; the pieces that make a team
work in concert:

- **PLAN / clusters** — the arbiter decomposes a task into a PLAN DAG and fans
  steps across a `cluster_id`; sub-agents run steps in parallel and the results
  fan back in (TASK_ASSIGN → TASK_PROGRESS → TASK_COMPLETE, correlated by
  `task_id`).
- **Reviewer critic loop (PR-MM3)** — a `reviewer` emits a verdict; on
  `NEEDS_REVISE` the `PlanExecutor` re-issues the step to the worker with the
  critique, up to `max_iterations`.
- **Orchestrator routing (PR-V6)** — send a task to the `orchestrator`; it
  deliberates and re-dispatches to the best-suited role with a
  `[ROUTE:role:reason]` marker (only a `can_route` role may route). Its routing
  reasoning shows in the Prompt stream (per-agent reasoning, PR-V5).
- **Cross-collective bridge (ACC-9)** — an agent emits `[DELEGATE:cid:reason]`
  to hand a subtask to a *peer collective* (gated by `bridge_enabled` / A-010),
  enabling two agentsets in different collectives to collaborate.
- **Reasoning stream** — with `reasoning_trace: true` roles, every participating
  agent's deliberation surfaces as a collapsible line in the Prompt screen
  (Ctrl+O expand, Ctrl+R hide), so you can *watch* the team think.

---

## 7. User stories — teams working a problem together

### Story A — "Ship a feature" (coding cell)
Apply `collective.coding-split.yaml` (or the §4 dedicated cell). Send the
architect a feature request via the Prompt screen. The **architect** decomposes
it into a PLAN; **implementers** build the pieces in parallel (cluster
`backend`); the **tester** writes/runs tests; the **reviewer** (strong model)
critiques and the critic loop re-issues weak steps until it passes. You watch
each agent's reasoning collapse-line in the transcript.

### Story B — "Research a market" (autoresearcher)
Swap to `collective.autoresearcher.yaml`. The **planner** frames the question;
the **economist / competitor / strategist** investigate in parallel from
different angles; the **synthesizer** merges their findings into a report; the
**critic** challenges gaps. One prompt → a multi-perspective brief.

### Story C — "Mixed inbox" (orchestrator-routed)
Apply `collective.orchestrator.yaml` with the `orchestrator` on a capable model.
Send tasks of mixed kinds to the `orchestrator`; it routes each to the right
worker — *"…requires code generation → coding_agent"*, *"…pattern recognition →
analyst"* — and the worker answers. The routing decision is visible, so the
operator sees **why** each task went where.

### Story D — "Pivot the team mid-session" (swap)
Run Story A's coding cell during a build sprint. When a strategy question comes
up, `./acc-deploy.sh apply collective.autoresearcher.yaml` — the coding cell is
swapped out, the research team spun up, the baseline + all episodic memory
intact. Swap back when you return to code. One stack, many specialised teams.

### Story E — "Two collectives, one problem" (cross-border)
Run a dev collective (`sol-01`, coding cell) and a research collective
(`sol-02`, autoresearcher) with the bridge enabled. When the dev cell hits a
question needing deep research, an agent emits `[DELEGATE:sol-02:needs market
analysis]`; the research collective works it and returns the result over the
ACC-9 bridge — two purpose-built agentsets collaborating across collectives.

---

## 8. Demo recipe (end-to-end)

```bash
# 1. Baseline up
./acc-deploy.sh up --webgui

# 2. Make sure the models your agentset references exist + are keyed
#    (models.yaml + LITELLM_API_KEY / ACC_ANTHROPIC_API_KEY in .env)

# 3. Spawn a team
./acc-deploy.sh apply --dry-run collective.orchestrator.yaml   # preview
./acc-deploy.sh apply collective.orchestrator.yaml             # spawn

# 4. Drive it from the TUI Prompt screen (target the orchestrator / architect),
#    watch the reasoning stream + PLAN fan-out + reviewer verdicts.

# 5. Pivot
./acc-deploy.sh apply collective.coding-split.yaml             # swap teams
```

## 9. Gotchas

- **Models need keys.** A keyed gateway (LiteLLM/OpenAI/Groq) agent needs
  `backend: openai_compat` in `models.yaml` + its `api_key_env` value in `.env`,
  reachable from the agent containers (host IP / service name, not localhost).
- **Routing authority is opt-in.** Only the `orchestrator` (a `can_route` role)
  re-dispatches; ordinary roles never route. Put the orchestrator on a capable
  model — small models route unreliably.
- **`apply` removes orphans.** Swapping replaces the previous agentset; that's
  intended, but `--dry-run` first if unsure.
- **Episodic memory is shared + survives** swaps/recreates (named volumes).

## See also
- [`docs/multimodel_reviewer.md`](multimodel_reviewer.md) — per-agent models, endpoints, API keys, the reviewer critic loop.
- [`docs/role-authoring.md`](role-authoring.md) — author new roles for your agentsets.
- [`docs/howto-coding-split-demo.md`](howto-coding-split-demo.md) — the coding-split demo in depth.
- `openspec/changes/20260526-role-proposal-orchestrator-multiagent-reasoning/` — the orchestrator + multi-agent reasoning design.
