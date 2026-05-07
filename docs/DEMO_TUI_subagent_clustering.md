# TUI Demo Walkthrough — Sub-agent Clustering

**Audience:** anyone showing the current state of sub-agent
clustering (PRs #26-#30 + #34-#38) to a stakeholder. Reads
top-to-bottom. Each step says **what to type**, **what to look at**,
and **what proves the feature works**.

**Pre-requisites:**

* `main` checked out — every PR in the series is merged
  (#26-#30 wire+UI; #34 arbiter resolver wiring; #35 CLI YAML;
  #36 stub skills; #37 personas; #38 the runnable scenario).
* `examples/coding_split_skills/.env` populated (copied from
  `.env.example` with at least the LLM credentials filled in).
* Nothing else — `run.sh` brings the stack up.

---

## Demo overview (~8 minutes)

| Phase | Time | TUI screen | Demonstrates |
|---|---|---|---|
| 0. Stack health | 1m | Soma (1) | Stack up, three coding agents registered. |
| 1. Cluster panel idle | 30s | Prompt (7) | `Clusters: 0` baseline. |
| 1.5. Environment prep | 30s | (CLI side) | `.env.example` → `.env`; `run.sh` invocation. |
| 2. Single-agent prompt | 1m | Prompt (7) | Back-compat — one prompt, one agent, no cluster. |
| 3. Cluster fan-out | 2m | Prompt (7) + Comms (4) | `/cluster show`, live topology, distinct `skill_in_use` per persona. |
| 4. Slash command intervention | 1m | Prompt (7) | `/cluster kill <cid>` cancels in flight. |
| 4.5. Programmatic verification | 30s | (CLI side) | `verify.sh` exit code + cluster summary. |
| 5. Markdown role authoring | 1m | (CLI side) | `acc-cli role compile/decompile/lint`. |
| 6. Compliance + audit | 30s | Compliance (3) + Performance (5) | Cat-A A-019 surfaced, per-skill invocation totals. |

---

## Phase 0 — stack health (`1` Soma)

Press **`1`**. Look at the **AGENTS** panel.

**What you should see**

* Three rows for the configured `coding_agent` instances (or the
  five personas if the persona PR has landed).
* Each agent's `state = ACTIVE`, `drift = 0.00`, `queue = 0`.
* `LLM` column populated (provider + model).

**Talk track**

> ACC's runtime is up. Each cell announced itself. Drift is zero
> because no work has flowed yet. We'll change that.

If any row shows `STALE`, fix that before continuing — the demo
relies on an active mesh.

---

## Phase 1 — cluster panel idle (`7` Prompt)

Press **`7`**. Note the layout:

* Top: target row (`Target role: coding_agent`, `Agent id: …`).
* **Just below**: cluster topology header reads
  `Clusters: 0`. Collapsed by default.
* Centre: empty transcript with `[dim]No prompts yet.[/dim]`
  placeholder.
* Bottom: prompt textarea + Send button + status line `Idle.`.
* NavBar shows the new 7th tab `Prompt`.

**What this proves**

* PR #29 cluster panel mounts cleanly without any cluster active.
* Status line ready, no errors during compose.

**Talk track**

> Prompt pane. The empty cluster header at the top is the
> topology view — it'll grow when sub-agent clusters spawn. Right
> now: no work, no clusters.

---

## Phase 1.5 — environment prep (CLI side)

Drop to a shell.  Show the `.env.example` template:

```bash
cat examples/coding_split_skills/.env.example | head -30
```

Copy + edit:

```bash
cp examples/coding_split_skills/.env.example examples/coding_split_skills/.env
$EDITOR examples/coding_split_skills/.env
# Set ACC_LLM_BACKEND + the matching credentials block.
```

**What this proves**

* The example is *self-contained* — every operator-configurable
  variable is declared in one file with comments explaining the
  three LLM-backend options.

**Talk track**

> Every demo deploy reads from one .env file.  Three LLM backends
> ship preconfigured — Anthropic, Ollama, OpenShift AI vLLM.  Pick
> one, paste the key, you're done.

---

## Phase 2 — single-agent prompt (back-compat)

Type:

```
Compute the factorial of 6.
```

Press **`Ctrl+S`** (or click **Send**).

**What you should see**

* The cluster header **stays at `Clusters: 0`** — single-agent
  tasks do not spawn clusters.
* `→ step N/M — <step_label>` lines stream into the transcript as
  the LLM works through the cognitive pipeline.
* A green `coding_agent-<id>` reply appears with the answer
  (`720`).
* Status line transitions: `Sent task_id=…`
  → `Reply received [ok] — agent=… latency=…ms`.

**What this proves**

* PR #26 wire-protocol back-compat: TASK_ASSIGN without
  `cluster_id` triggers single-agent dispatch on the agent side
  *and* registers no cluster on the TUI side.
* PR-19 streaming still works (the trend arrows render in the
  transcript).

**Talk track**

> Notice the cluster panel didn't move — clustering kicks in only
> when a PLAN step's role configures it. Ad-hoc prompts stay
> single-agent. Same wire shape ACC has had for months.

---

## Phase 3 — cluster fan-out

Submit the example plan that exercises the implementer fan-out
(reference `examples/coding_split_skills/plan.yaml`).

```bash
# In a second terminal:
./examples/coding_split_skills/run.sh
```

`run.sh` sources `.env`, lints every persona's role.md, and submits
the plan via `acc-cli plan submit`.  The personas already exist on
disk (PR #37) and the arbiter's resolver wiring (PR #34) consults
each step's role.estimator block.

Switch back to the TUI, **`7`** Prompt pane.

**Within ~5 seconds**

The cluster header changes. Click it (or press the panel's
expand chevron) to expand:

```
▼ Clusters: 2 (Σ 2 agents)
  c-arch1234 · coding_agent_architect · 1 agents · fixed strategy, count=1
    ● coding_agent_arch-aaa · skill:code_review · step 4/6 · running
  c-deps5678 · coding_agent_dependency · 1 agents · fixed strategy, count=1
    ● coding_agent_dep-bbb  · skill:dependency_audit · step 3/6 · running
```

**Within ~15 seconds**

The architect finishes. The implementer cluster spawns 3 members.
The header updates to `Clusters: 2 (Σ 4 agents)` (architect cluster
still visible during 30 s grace window):

```
▼ Clusters: 2 (Σ 4 agents)
  c-arch1234 · coding_agent_architect · 1 agents · fixed strategy, count=1
    ● coding_agent_arch-aaa · skill:code_review · step 6/6 · complete
  c-impl9012 · coding_agent_implementer · 3 agents · 6300 tokens, +1 difficulty
    ● coding_agent_impl-ccc · skill:code_generation · step 1/6 · running
    ● coding_agent_impl-ddd · skill:code_generation · step 1/6 · running
    ● coding_agent_impl-eee · skill:code_generation · step 1/6 · running
```

**Type a slash command in the prompt textarea:**

```
/cluster show
```

Press **`Ctrl+S`**. A `system` block appears in the transcript with
the same topology, rendered as text — useful when the panel is
collapsed or when you want a chronological record.

**Press `4`** to switch to Comms.

The PLAN DAG renders five rows; the implementer step shows
`RUNNING`. Knowledge_feed lists the architect's `draft_interface`
KNOWLEDGE_SHARE.

**What this proves**

* PR #26 cluster_id is on the wire (the panel populates from echoed
  TASK_PROGRESS).
* PR #27 estimator + fan-out: 1-agent for `fixed: 1`, 3-agent for
  the heuristic with the `+1 difficulty` bump from the
  implementer's role config.
* PR #29 panel renders both the single-agent clusters and the
  fan-out cluster, with per-member skill column.
* PR #30 `/cluster show` slash command works end-to-end without
  leaving the screen.

**Talk track**

> The architect runs alone — `fixed: 1` says only one architect
> per task; multiple architects fragment the design. The
> implementer fan-out is heuristic-driven: the role's estimator
> reads tokens + a "concurrency" keyword bump and picks 3
> members. The cluster_id binds them. Skills come from the
> persona's allow-list — the panel reads the live skill column
> from each member's progress emit.

---

## Phase 4 — cluster intervention via slash command

Find the implementer cluster_id from the panel (e.g.
`c-impl9012`).

Type in the prompt textarea:

```
/cluster kill c-impl9012
```

Press **`Ctrl+S`**.

**What you should see**

* A `system` block appears: `cancel requested for cluster c-impl9012`.
* Within a few seconds (once the agent-side cancel handler lands;
  see § Known gaps below), each member of `c-impl9012` reports
  `TASK_COMPLETE blocked=True block_reason="cancelled"`.
* The panel marks each member's status `blocked` (red dot).
* The cluster's `finished_at` is stamped; the cluster row
  disappears from the panel after the 30 s grace window.
* Comms screen: implement step → FAILED, downstream test + review
  cascade to FAILED.

**What this proves**

* PR #30 operator-side cancel publish on the TUI.
* PR #30 wire shape: `acc.{cid}.task.cancel` carrying `cluster_id`.
* PR #27 aggregation: one-blocked-fails-step semantics on the
  arbiter side.

**Talk track**

> Operator interventions live in the prompt pane now — type
> `/cluster kill <id>` and the cluster aborts cleanly. The
> follow-on PLAN steps cascade to FAILED because their
> `depends_on` is unresolved. No screen-switch, no kubectl, no
> pager.

---

## Phase 4.5 — programmatic verification

After ~90 s of run time:

```bash
./examples/coding_split_skills/verify.sh
```

Subscribes to `acc.${ACC_COLLECTIVE_ID}.>` for the configured
window, parses every payload's `cluster_id`, and prints a
unique-cluster summary:

```
Clusters observed:
  c-arch1234   members=1
  c-deps5678   members=1
  c-impl9012   members=3
  c-test3456   members=2
  c-rev7890    members=1

Distinct clusters: 5 (min required: 3)
✓ Verify OK — cluster fan-out reached the minimum threshold.
```

Exit code is 0 when ≥ `ACC_VERIFY_MIN_CLUSTERS` distinct clusters
were observed — the same script suits CI smoke-tests.

**What this proves**

* The cluster panel and the bus *agree* on the topology — verify.sh
  reads only the bus, not the TUI.  If the panel showed clusters
  that verify.sh didn't see, that would indicate a TUI-side bug.

**Talk track**

> The whole demo is reproducible.  Verify.sh runs unattended, exits
> 0 on a healthy run.  Drop it into a CI pipeline and you have a
> nightly clustering smoke-test.

---

## Phase 5 — markdown role authoring (CLI side)

Drop to a shell:

```bash
acc-cli role list
# coding_agent
# coding_agent_architect
# coding_agent_dependency
# coding_agent_implementer
# coding_agent_reviewer
# coding_agent_tester
# (plus every other role under roles/)

acc-cli role decompile coding_agent > /tmp/coding_agent.md
$EDITOR /tmp/coding_agent.md
# Bump max_parallel_tasks: 5, save.

acc-cli role lint /tmp/coding_agent.md
# /tmp/coding_agent.md: clean

acc-cli role compile /tmp/coding_agent.md --dest /tmp/out
ls /tmp/out/
# coding_agent/role.yaml
# coding_agent/system_prompt.md
```

**What this proves**

* PR #28 round-trip identity: `decompile` of a real bundled role
  produces markdown that `compile` parses back without losing
  data.
* `lint` catches malformed sources before they reach the runtime.
* The estimator block, capabilities, and system prompt all live
  in one human-friendly file.

**Talk track**

> Roles live in markdown — operators don't need to know the
> pydantic schema. The compiler emits the canonical YAML the
> runtime consumes, the decompiler is the migration tool, and
> lint is the safety net. New personas are a `cp` away.

---

## Phase 6 — compliance + audit cross-check

Press **`3`** Compliance. Look for:

* No A-019 violations should appear during the demo (the
  estimator and the in-process clamp keep clusters in bounds).
* Oversight queue: empty unless a HIGH-risk task was injected.

Press **`5`** Performance. Look at the **CAPABILITIES** panel:

* Per-skill rows: `code_review`, `code_generation`,
  `test_generation`, `dependency_audit`. Each shows total / ok /
  fail / ok_rate.
* Per-MCP rows similar (only `echo_server.echo` if `MCP_ECHO=true`).

**What this proves**

* PR-telemetry (existing) still aggregates per-skill totals
  across cluster members exactly as for single agents — the
  capability_stats fold is per `(kind, target)` regardless of
  cluster membership.
* Cat-A enforcement audit log carries the cluster context
  (cluster_id is on every signal).

**Talk track**

> Compliance + Performance close the loop. Every skill the
> cluster invoked is counted. If A-019 had fired, it would show
> here. ACC's audit story is unchanged by clustering — it just
> got more parallel.

---

## Known gaps to acknowledge during the demo

If a stakeholder asks, here's the honest state as of PR #38:

* **Agent-side cancel.** PR #30 publishes `TASK_CANCEL`; the
  agent-side cooperative checkpoint inside
  `CognitiveCore.process_task` is a documented short-term
  follow-up (`docs/ROADMAP_subagent_clustering.md` § S1).  Until
  it merges, the cancel signal is observable on the bus but
  members keep working — the demo's `/cluster kill` is
  rhetorically correct but operationally a no-op for ~30 s
  longer than it should be.
* **Mixed-role clusters.** Today every member of one cluster is
  the same role.  The runnable scenario works around this by
  spawning one cluster per persona, with KNOWLEDGE_SHARE +
  scratchpad rendezvous between them.  Heterogeneous clusters
  are ROADMAP M1.
* **`/oversight` slash verbs.** Parser accepts them; screen
  responds with a "use Compliance screen" hint.  Full wiring
  follows.
* **SubtraceModal.** A dedicated modal that opens the per-member
  cognitive-step trace was scoped out of PR #29.  The Performance
  screen telemetry covers most of what the modal would show.
* **Real skill execution.** The six coding-cluster skills
  (`code_review`, `code_generation`, ...) are LOW-risk pass-through
  stubs (PR #36 / D4).  The LLM still does the actual work via
  natural-language output; the skill registry contributes Cat-A
  enforcement + audit anchors only.  Replacing each adapter with
  a real linter / static-analysis / pytest backend is a separate
  hardening track.

---

## What to say if something goes wrong

| Symptom | Diagnosis | Backup talking point |
|---|---|---|
| `Clusters: 0` while plan is in flight | `cluster_id` propagation broke | "Let me confirm the build — this should always be > 0 here." Skip to Phase 5 (CLI side) which doesn't depend on the panel. |
| `acc-cli plan submit` errors with `target_role unknown` | Persona role files not on disk | Substitute a single-role plan submitting `coding_agent` three times via `task_description` text. The cluster panel still demonstrates with the `target_role: coding_agent` cluster. |
| Slash command echoes `unknown command` | Parser changed but verb missing | Stick to `/help` to show the parser is alive; explain the verb you intended. |
| Pilot-run TUI fails to start | Local Textual version drift | `pip install -U textual`. The widget code targets Textual ≥ 0.80. |
