# Enhanced `coding_agent` + Dedicated Sub-agent Personas

**Companion to:** `IMPLEMENTATION_subagent_clustering.md`,
`SUBAGENT_COMMUNICATION.md`.

This document specifies how the existing `coding_agent` role
(`roles/coding_agent/role.yaml`) **should evolve** to take advantage
of PR #26–#30 cluster fan-out. It defines five named sub-agent
personas, their skill mix, communication patterns, and the
estimator config that selects between them.

> **Status:** **live as of PR #37**. All five persona role
> directories ship under `roles/coding_agent_*/` with role.md +
> role.yaml + system_prompt.md + eval_rubric.yaml.  The runnable
> showcase using these personas lives at
> `examples/coding_split_skills/` (PR #38).  This document is now
> the persona reference, not the design proposal.

---

## Why dedicated personas

Today's `coding_agent` role has one persona (`analytical`) and one
system prompt that does everything. The `[SKILL: ...]` markers in
the LLM output are how the model decides which skill to invoke.

That works for single-agent runs. It does **not** work well for
clusters because every member loads the same prompt + the same skill
catalogue. Members become indistinguishable — they all pull toward
the same kind of output, and the cluster's parallel work converges.

Personas fix this: each member is told it has *one* job, runs *one*
or *two* skills, and integrates with siblings via a documented
communication pattern. The arbiter's `slice_skill_mix` round-robin
becomes meaningful.

---

## Five named personas

| Persona | Default skills | Domain receptors | Default communication |
|---|---|---|---|
| **architect** | `code_review` | `software_engineering` | Pattern B — leads, publishes early `KNOWLEDGE_SHARE` |
| **implementer** | `code_generation` | `software_engineering` | Pattern A — independent; consumes architect's draft when present |
| **reviewer** | `code_review`, `security_scan` | `software_engineering`, `security_audit` | Pattern A — runs after the implementer's draft lands |
| **tester** | `test_generation`, `test_execution` | `software_engineering`, `testing` | Pattern A + autocrine EVAL_OUTCOME |
| **dependency_auditor** | `dependency_audit`, `security_scan` | `security_audit`, `dependency_management` | Pattern A — independent; orthogonal to other members |

Each persona is a *narrowed* `coding_agent` — same role family, same
Cat-A bounds, same risk ceilings — with a distinct system prompt,
default skill set, and receptor set. No new pydantic schema is
needed; the personas live as **separate role directories** under
`roles/`:

```
roles/
├── _base/                       # unchanged
├── coding_agent/                # general-purpose; default for ad-hoc tasks
├── coding_agent_architect/      # NEW
├── coding_agent_implementer/    # NEW
├── coding_agent_reviewer/       # NEW
├── coding_agent_tester/         # NEW
└── coding_agent_dependency/     # NEW
```

The cluster fan-out path (PR #27) accepts heterogeneous skill_mix
slices today but keeps every member at the same `target_role`.
Mixed-role clusters are deferred to a follow-up PR (noted in
`IMPLEMENTATION_subagent_clustering.md` § Known gaps).  **The
runnable showcase therefore submits a multi-step PLAN where each
step has its own persona — one persona per cluster, multiple
clusters per plan.**  The architect's KNOWLEDGE_SHARE pattern is how
peer personas in different clusters of the same plan share state.

---

## Persona deep-dives

### 1. `coding_agent_architect`

**One-line purpose.** Define interfaces, file layout, and module
boundaries before any implementation begins.

**System prompt sketch** (excerpt — the canonical version lives in
`roles/coding_agent_architect/system_prompt.md` once the role lands):

```markdown
You are a precise software architect.  Your single job is to draft
the *interface* and *file layout* for the requested change.  Do NOT
write implementation bodies.  Do NOT write tests.  Emit:

  - A short prose summary of the design decision.
  - A list of files (path, description).
  - For each file, a header-only sketch (function signatures, type
    hints, docstrings).  Stop at `raise NotImplementedError` or `pass`.

When the cluster has more than one member, emit a `[SKILL:
code_review]` invocation on your own draft to self-validate before
publishing.

Always emit a `KNOWLEDGE_SHARE` with `domain_tag=code_patterns,
knowledge_type=draft_interface, content=<your draft>` so peer
implementers and reviewers have a canonical reference.

Confidence:
  - 1.0 when every public symbol has a name + signature + docstring.
  - 0.5 when only file layout is stable.
```

**Estimator config (in role.yaml).**

```yaml
estimator:
  strategy: "fixed"
  fixed:
    count: 1
```

The architect persona is **always single-instance**. Two architects
on one task drift apart and produce conflicting drafts.

**Default communication pattern.** Pattern B (knowledge-share fan-in).
Architect's draft is the reference siblings consume.

---

### 2. `coding_agent_implementer`

**One-line purpose.** Fill in the implementation bodies for one or
more modules, given a stable interface (from architect or from the
inbound task description).

**Skills.** `code_generation` (default), `code_review` (allowed for
self-check).

**System prompt sketch.**

```markdown
You are a precise software implementer.  Your job is to produce
RUNNING code for the requested module.  When a `KNOWLEDGE_SHARE` of
type `draft_interface` is in scope (read it from the scratchpad
before you start), treat it as authoritative — do not redesign.

For each file, emit:
  - The full file body.
  - Inline comments where the design choice is non-obvious.
  - One `[SKILL: code_review]` invocation on your own output when
    confidence < 0.8.

Do NOT write tests.  A peer tester member handles that.

If you can't satisfy the interface — flag a `[SKILL: code_review]`
with the conflict in `notes`.  Do NOT silently restructure.
```

**Estimator config.** Pattern A (static decomposition).

```yaml
estimator:
  strategy: "heuristic"
  heuristic:
    base: 1
    per_n_tokens: 1500
    cap: 4
  difficulty_signals:
    - keyword: "concurrency"
      bump: 1
    - keyword: "refactor"
      bump: 1
```

Multiple implementers in one cluster get sliced files via
`slice_skill_mix` round-robin. The architect's KNOWLEDGE_SHARE
provides the file → module mapping so members do not collide.

---

### 3. `coding_agent_reviewer`

**One-line purpose.** Read the implementer's output, surface
correctness + style issues, escalate security findings.

**Skills.** `code_review` (default), `security_scan` (default).

**System prompt sketch.**

```markdown
You are a strict code reviewer.  Your job is to read implementer
output and answer two questions:

  1. Does this satisfy the architect's `draft_interface`?
  2. Does this introduce a security or correctness regression?

Emit a single JSON verdict (PASS | FAIL | NEEDS_CHANGES) with a list
of findings.  Each finding has severity, line, message.  CRITICAL
or HIGH security findings MUST trigger an `ALERT_ESCALATE` immediately
— do not wait for the rest of the review.

You read from the cluster scratchpad:
  - acc:<cid>:cluster:<cid>:draft_interface  (architect's draft)
  - acc:<cid>:cluster:<cid>:implementations  (collated impl outputs)
```

**Estimator config.** Single-instance per cluster — multiple
reviewers fragment the verdict.

```yaml
estimator:
  strategy: "fixed"
  fixed:
    count: 1
```

**Communication.** Pattern A (independent) but reads scratchpad
written by other personas (Pattern C).

---

### 4. `coding_agent_tester`

**One-line purpose.** Write + run unit and integration tests against
the implementer output.

**Skills.** `test_generation` (default), `test_execution` (default).

**System prompt sketch.**

```markdown
You are a precise test author.  Given an implementation, produce:

  - One pytest module per source file under test.
  - At least one positive case + one boundary case + one negative
    case per public symbol.
  - For symbols affecting external IO, a fixture-isolated case.

After generation, invoke `[SKILL: test_execution]` to run the suite
in a sandboxed scratchpad.  Emit an `EVAL_OUTCOME` against the role's
eval_rubric so the arbiter can rank cluster outputs.

You do NOT modify the implementation.  Failures go in the test
verdict.
```

**Estimator config.**

```yaml
estimator:
  strategy: "heuristic"
  heuristic:
    base: 1
    per_n_tokens: 3000   # tests parallelise by file, but lower than impl
    cap: 3
```

**Communication.** Pattern A + autocrine EVAL_OUTCOME (Pattern D).

---

### 5. `coding_agent_dependency` (dependency_auditor)

**One-line purpose.** Audit `pyproject.toml` / `requirements.txt` /
`package.json` style declarations for known-vulnerable versions and
license incompatibilities.

**Skills.** `dependency_audit` (default), `security_scan` (default).

**System prompt sketch.**

```markdown
You are a dependency auditor.  For every declared dependency:

  1. Resolve the actual version range to a concrete latest matching
     version.
  2. Cross-check against your CVE knowledge.
  3. Check license compatibility against the role's allowed_licenses
     list (default MIT/Apache-2.0/BSD).

Emit a JSON report: dependency, current_version_range, resolved,
cve_findings (CVE-ID, severity, affected_versions), license_findings.
ESCALATE on CRITICAL CVEs immediately via `ALERT_ESCALATE`.

You do NOT change source code.  Recommendations go in the report
for a follow-on `coding_agent_implementer` cluster step.
```

**Estimator config.**

```yaml
estimator:
  strategy: "fixed"
  fixed:
    count: 1
```

**Communication.** Pattern A — fully independent. Output is fed
into a downstream PLAN step (not a sibling cluster member) so the
results can be acted on as their own decision.

---

## End-to-end task example: "Add an HTTP rate limiter"

Operator submits a single PLAN with four steps:

```yaml
plan:
  plan_id: "ratelimiter-2026-05"
  steps:
    - step_id: "design"
      role: "coding_agent_architect"
      task_description: |
        Design a token-bucket HTTP rate limiter for the FastAPI
        gateway.  Public surface, file layout, and module boundaries
        only.

    - step_id: "implement"
      role: "coding_agent_implementer"
      depends_on: ["design"]
      task_description: |
        Implement the design from the cluster scratchpad
        draft_interface.  One module per file as outlined.

    - step_id: "test"
      role: "coding_agent_tester"
      depends_on: ["implement"]
      task_description: |
        Generate pytest cases for every public symbol in the
        implementation.  Run the suite.

    - step_id: "review"
      role: "coding_agent_reviewer"
      depends_on: ["implement"]
      task_description: |
        Read the implementer output + tester output.  Produce a
        verdict (PASS|NEEDS_CHANGES|FAIL).  Escalate any HIGH
        security findings.

    - step_id: "deps"
      role: "coding_agent_dependency"
      depends_on: []
      task_description: |
        Audit pyproject.toml for new dependencies the implementer
        may have added (re-check after `implement` lands).
```

What the arbiter does:

1. `design` and `deps` start in parallel (no `depends_on`). Each
   resolves to a single-member cluster (their estimator is `fixed:
   1`).
2. `design` finishes; publishes `KNOWLEDGE_SHARE
   draft_interface` + writes `acc:<cid>:cluster:<cid>:draft_interface`
   to the scratchpad.
3. `implement` becomes ready. Estimator returns N=2 or 3 sub-agents
   (depending on the task size). The cluster spawns; round-robin
   slices file ownership.
4. Each implementer reads the scratchpad rendezvous, produces its
   slice, writes to the scratchpad.
5. `implement` completes when all members report. `test` and
   `review` become ready in parallel.
6. `test` cluster produces tests + runs them; emits EVAL_OUTCOME.
7. `review` cluster (single member) reads scratchpad + produces
   verdict.
8. Plan terminal — TUI Comms screen renders the full DAG, prompt
   pane cluster panel shows live topology while in flight.

---

## Operator surface

### Selecting personas in the prompt pane

The `Target role` dropdown lists every role under `roles/`. Operators
pick the persona for ad-hoc clusters; for full multi-step plans they
submit the plan via `acc-cli plan submit <plan.yaml>` (existing CLI
in `acc/cli/plan_cmd.py`).

### Authoring a new persona

```bash
$EDITOR roles/coding_agent_<new>/role.md
acc-cli role lint roles/coding_agent_<new>/role.md
acc-cli role compile roles/coding_agent_<new>/role.md
```

Compilation writes `role.yaml` + `system_prompt.md` next to the
source. PR #28's round-trip identity guarantee makes the markdown
the canonical authoring format.

### Controlling cluster size at runtime

Two knobs:
* `role.max_parallel_tasks` — hard ceiling. Cat-A A-019 enforces.
* `role.estimator.heuristic.cap` — soft cap inside the heuristic;
  always clamped down by `max_parallel_tasks`.

Bumping `max_parallel_tasks` requires editing role.md + recompiling.
Bumping the estimator cap is an operator decision per persona.

---

## Migration plan from today's `coding_agent`

**Phase 1 — additive.** Land the five new persona role directories
side-by-side with the existing `coding_agent`. No migration of
existing references.

**Phase 2 — coding-split demo update.**
`examples/coding_split/plan.yaml` switches from three
`coding_agent` instances to one architect + two implementers + one
tester + one reviewer.

**Phase 3 — operator habit.** Document personas in the prompt-pane
how-to (`acc/tui/help/prompt.md`). Once operators reach for the
personas naturally, the bare `coding_agent` role becomes the
"miscellaneous" fallback.

**Phase 4 — heterogeneous clusters.** Once the deferred mixed-role
cluster work lands (a follow-up PR after #30), one cluster can hold
an architect + N implementers + a reviewer simultaneously. The
five personas are pre-validated for that future and need no schema
changes.

---

## Acceptance criteria — when this design "lands"

* `roles/coding_agent_architect/`, `..._implementer/`,
  `..._reviewer/`, `..._tester/`, `..._dependency/` all exist with
  role.md + role.yaml + system_prompt.md + eval_rubric.yaml.
* `acc-cli role list` shows them all.
* `acc-cli role lint` passes for every persona.
* `acc-cli role show coding_agent_architect` displays the
  estimator block correctly.
* The TUI prompt-pane target dropdown shows all five personas.
* `examples/coding_split_skills/` (next document) contains a
  runnable scenario submitting a four-step plan that exercises
  every persona.
* The cluster panel renders the architect / implementer
  cluster_ids correctly with skill_in_use populated from the
  members' step labels.

---

## How the showcase actually runs (PR #38)

The runnable scenario lives at `examples/coding_split_skills/` and
uses every persona above.  Top-level flow:

```bash
# 1. Configure the LLM backend
cp examples/coding_split_skills/.env.example examples/coding_split_skills/.env
$EDITOR examples/coding_split_skills/.env       # set ACC_*_API_KEY

# 2. One-command run
./examples/coding_split_skills/run.sh

# 3. Watch live in the TUI
acc-tui                                          # press 7 for the cluster panel

# 4. Programmatic check
./examples/coding_split_skills/verify.sh         # exit 0 when ≥ N clusters

# 5. Tear down
./examples/coding_split_skills/clean.sh
```

The plan (`examples/coding_split_skills/plan.yaml`) submits five
DAG steps — one per persona — connected via `depends_on` edges.  The
arbiter's PlanExecutor (PR #34) consults each step's role.estimator
block and fans the implementer + tester steps out into multi-member
clusters; architect / reviewer / dependency steps stay
single-instance per their `fixed: 1` strategy.

`.env.example` documents every operator-configurable variable; the
runner sources `.env` so anything declared there flows through to
both `acc-deploy.sh` and `acc-cli`.

## Eval rubric summary across personas

| Persona | Heaviest weight | Security weight | Total |
|---|---|---|---|
| `coding_agent_architect` | contract_completeness 0.35 | 0.10 | 1.00 |
| `coding_agent_implementer` | correctness 0.35 | 0.15 | 1.00 |
| `coding_agent_reviewer` | finding_accuracy 0.35 | 0.25 | 1.00 |
| `coding_agent_tester` | test_coverage 0.30 | 0.15 | 1.00 |
| `coding_agent_dependency` | cve_recall 0.35 | 0.25 | 1.00 |

All five satisfy the schema invariant pinned in
`tests/test_coding_agent_personas.py` (weights sum to 1.0,
security ≥ 10%).

## Cancellation behaviour summary

Per persona — keyed off `TASK_CANCEL` carrying the step's task_id
or the cluster's cluster_id:

| Persona | On cancel |
|---|---|
| architect | Publish the partial draft via KNOWLEDGE_SHARE before exit. |
| implementer | Abandon the slice; do NOT publish a half-written impl_ready. |
| reviewer | Emit verdict NEEDS_CHANGES (never PASS on partial review). |
| tester | Emit EVAL_OUTCOME with verdict=PARTIAL. |
| dependency | Emit the partial dependency report as-is. |

> **Note:** the agent-side TASK_CANCEL handler is itself still a
> short-term follow-up (ROADMAP S1).  Until it lands, the operator
> publishes the cancel signal but members continue running — the
> per-persona cancellation contracts above describe what each
> persona's system prompt instructs the LLM to do *once* the
> cooperative checkpoint inside `CognitiveCore.process_task` lands.
