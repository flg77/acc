# Example — Coding Split with Dedicated Skills

Fully runnable showcase of sub-agent clustering with five named
personas working a single coding task in parallel.

## What this demonstrates

* **Cluster fan-out per PLAN step** — one PLAN step expands into
  multiple sub-agents working in parallel.  Different personas use
  different estimator strategies (`fixed: 1` for architect / reviewer
  / dependency_auditor; `heuristic` for implementer + tester).
* **Skill-aware role personas** — architect, implementer, reviewer,
  tester, dependency_auditor each load distinct skill prompts.  The
  cluster panel's `skill_in_use` column reads from real,
  registry-loaded skills.
* **Mixed communication patterns** — Pattern B (knowledge-share
  fan-in) between architect and implementers; Pattern C (scratchpad
  rendezvous) for the implementer artefact; Pattern A (independent)
  for tester + dependency_auditor; Pattern D (autocrine
  EVAL_OUTCOME) for tester self-grading.
  See `docs/SUBAGENT_COMMUNICATION.md` for the full pattern catalogue.
* **Live TUI cluster panel** rendering the topology end-to-end.
* **Cat-A A-019 enforcement** — every persona's `max_parallel_tasks`
  caps the cluster size.
* **Operator slash commands** — `/cluster show`, `/cluster kill <cid>`
  to interrupt mid-flight.

## Task

> *Add a token-bucket HTTP rate limiter to a FastAPI gateway. Cover
> per-IP, per-API-key, and global ceilings. Tests included. Audit
> any new dependencies for known CVEs.*

The task body lives in `task.md` for ad-hoc operator submissions; the
plan-driven multi-cluster version is in `plan.yaml` (and the JSON
copy `plan.json` for environments without YAML support).

## Plan structure (DAG)

```
              ┌──── design ────────────────────────────┐
              │  coding_agent_architect (1 member)     │
              │   → KNOWLEDGE_SHARE draft_interface    │
              └────────────────┬───────────────────────┘
                               │ depends_on
              ┌────────────────▼───────────────────────┐
              │  implement                             │
              │  coding_agent_implementer (≥2 members) │
              │   → file slices via skill_mix          │
              │   → scratchpad: per-file impl outputs  │
              └─────┬───────────────┬──────────────────┘
   depends_on       │               │  depends_on
              ┌─────▼──────┐  ┌─────▼──────────────────┐
              │  test      │  │  review                │
              │  tester ≥2 │  │  reviewer (1 member)   │
              └────────────┘  └────────────────────────┘

              ┌────────────────────────────────────────┐
              │  deps                                  │
              │  coding_agent_dependency (1 member)    │
              │  no depends_on — runs from start       │
              └────────────────────────────────────────┘
```

## Prerequisites

1. ACC repo cloned + `pip install -e .` done in a `.venv`.
2. `acc-deploy.sh` works locally (i.e. podman + podman-compose
   installed; `./acc-deploy.sh up` boots the stack on a fresh
   checkout).
3. An LLM backend reachable from the agent containers.  The runner
   reads `ACC_LLM_BACKEND` from `.env` — Anthropic, Ollama, and any
   OpenAI-compatible endpoint (vLLM, OpenShift AI) are all
   supported.

## Run it (one command)

```bash
# 1. Set up env
cd /path/to/agentic-cell-corpus
cp examples/coding_split_skills/.env.example examples/coding_split_skills/.env
$EDITOR examples/coding_split_skills/.env   # set LLM creds

# 2. Run
./examples/coding_split_skills/run.sh
```

`run.sh` will:
1. Source `.env` into the process environment.
2. `acc-cli role lint` every persona's `role.md` — fails fast on a
   schema regression.
3. `./acc-deploy.sh up` (TUI + MCP_ECHO profiles per .env).
4. `acc-cli plan submit examples/coding_split_skills/plan.yaml` to
   the active collective.
5. Print follow-up commands.

To watch PLAN re-broadcasts streaming live:

```bash
./examples/coding_split_skills/run.sh --watch
```

## Watch the topology in the TUI

```bash
acc-tui          # in another terminal
# Press 7 (Prompt) — cluster panel above the transcript shows
#   ▼ Clusters: N (Σ M agents)
#   c-arch1234 · coding_agent_architect · 1 agents
#     ● coding_agent_architect-aaa · skill:code_review · running

# Type a slash command in the prompt textarea:
> /cluster show              ← snapshot in transcript
> /cluster kill c-impl9012   ← cancel the implementer cluster

# Press 4 (Comms) — full PLAN DAG with step_progress map.
```

## Programmatic verification

After the run, check that the expected topology actually appeared:

```bash
./examples/coding_split_skills/verify.sh
```

Subscribes to `acc.${ACC_COLLECTIVE_ID}.>` for
`${ACC_VERIFY_DURATION_S}` seconds, parses `cluster_id` values,
prints unique clusters + member counts. Exit code 0 when ≥
`${ACC_VERIFY_MIN_CLUSTERS}` distinct cluster_ids were observed.
Suitable for CI smoke-tests.

## Tear down

```bash
./examples/coding_split_skills/clean.sh
```

Stops the stack and evicts cluster-scoped scratchpad keys
(`acc:${cid}:cluster:*`).

## Files in this directory

| File | Purpose |
|---|---|
| `.env.example` | Template — copy to `.env` and fill in. |
| `plan.yaml` | Canonical plan (5 steps, 5 personas). |
| `plan.json` | JSON copy for environments without YAML support. |
| `task.md` | Operator-facing prose version of the same task. |
| `expected_topology.md` | Phase-by-phase reference + anti-checks. |
| `run.sh` | One-shot runner — sources .env, lints, deploys, submits. |
| `verify.sh` | Programmatic post-run cluster check (CI-friendly). |
| `clean.sh` | Teardown + scratchpad eviction. |
| `README.md` | This file. |

## Brainstorm — other tasks worth running through this scenario

| Task type | Why it's a good fit |
|---|---|
| **Generate a CLI command from spec** | architect → implementer (one) → tester. Linear; minimum cluster fan-out. |
| **Write a connector plugin (REST API)** | architect → implementer (skills: code_generation + http_client) + reviewer + dependency_auditor in parallel. |
| **Refactor a 1k-line module split into smaller files** | implementer cluster of 4-5 with refactor difficulty bump → +1 sub-agent. |
| **Security-audit a piece of crypto code** | reviewer + security_scan skill at MEDIUM risk; escalates HIGH findings via ALERT_ESCALATE. |
| **Convert ESM to CommonJS in a TypeScript package** | implementer cluster — tests still authored by tester persona. |
| **Migrate from pydantic v1 to v2 across a codebase** | architect (defines the rewrite recipe) → implementer cluster (per-module slicing) → tester. |
| **Write a deserialiser for a binary format** | implementer (1) + tester (1) — test cluster size scales with edge cases. |
| **Generate Terraform for a new Kubernetes namespace** | implementer with low parallelism + reviewer for compliance + dependency_auditor for module versions. |
| **Add a new MCP server manifest** | implementer (writes mcp.yaml) + reviewer (validates risk_level + allow-list). Single cluster pair. |
| **Fix a flaky pytest** | tester persona; cluster of 1 (reproducer) → 2 (orthogonal candidate fixes). |

The common pattern: **decompose along skill lines**, not along file
lines. The arbiter's `slice_skill_mix` round-robin distributes
files across members within a cluster anyway.

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---|---|---|
| `target_role unknown` on every TASK_ASSIGN | Persona role directories not present | Confirm `roles/coding_agent_*/role.yaml` exist on the agent host. The `coding_agent_*` family ships with PR #37. |
| Cluster panel header stays at `Clusters: 0` | Arbiter not wired with resolvers | Confirm PR #34 (D1) is on the agent host's main. |
| `acc-cli plan submit` rejects the YAML | YAML support missing | Confirm PR #35 (D2) is on the agent host's main. |
| `/cluster kill` published but members keep working | Agent-side cancel handler not yet implemented | Known follow-up — see `docs/ROADMAP_subagent_clustering.md` § S1. |
| `run.sh` says "No .env at …" | `.env` not yet created | `cp .env.example .env && $EDITOR .env`. |
| `verify.sh` exits non-zero with `Distinct clusters: 0` | Plan didn't execute or LLM not reachable | Check `./acc-deploy.sh logs` for arbiter errors; bump `ACC_VERIFY_DURATION_S`. |

## See also

* `docs/IMPLEMENTATION_subagent_clustering.md` — the wire-protocol + module reference.
* `docs/SUBAGENT_COMMUNICATION.md` — Patterns A–E in detail.
* `docs/CODING_AGENT_SUBROLES.md` — full persona deep-dives.
* `docs/DEMO_TUI_subagent_clustering.md` — the keystroke-by-keystroke
  TUI walkthrough that pairs with this scenario.
* `docs/ROADMAP_subagent_clustering.md` — what comes next.
