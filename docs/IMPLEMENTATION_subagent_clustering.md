# Sub-agent Clustering — Implementation Details

**Status:** PR #26 → PR #30 open on `flg77/acc`. None merged at the time
of writing. Local test totals: 121 passing across all related modules
(see "Test matrix" below).

This document records exactly how sub-agent clustering is implemented
across the five-PR series outlined in `PLAN_subagent_clustering.md`.
Read alongside that plan for design rationale; this file is the
**reference for what shipped**.

> **PR numbering used in this document mirrors the GitHub PRs:**
>
> | Plan PR | GitHub PR | Branch | Stack base |
> |---|---|---|---|
> | PR-1 | [#26](https://github.com/flg77/acc/pull/26) | `feat/cluster-id-propagation` | `main` |
> | PR-2 | [#27](https://github.com/flg77/acc/pull/27) | `feat/estimator-and-spawn` | PR #26 |
> | PR-3 | [#28](https://github.com/flg77/acc/pull/28) | `feat/markdown-role-authoring` | `main` (independent) |
> | PR-4 | [#29](https://github.com/flg77/acc/pull/29) | `feat/cluster-tui-surface` | PR #27 |
> | PR-5 | [#30](https://github.com/flg77/acc/pull/30) | `feat/prompt-slash-commands` | PR #29 |

---

## Why clustering at all

ACC's existing `PLAN` signal already lets the arbiter dispatch parallel
DAG steps. What it did *not* support was **one PLAN step that fans out
into several sub-agents working on the same step in parallel**. That
capability is what every "agentic coding" demo on the market shows
operators in 2025–2026: dispatcher hands a task to N specialised
workers, each with a different skill slice, fan-in aggregates the
result.

ACC's contribution is to do this with the four invariants no public
agent runtime currently enforces together:

1. **Wire-protocol back-compat.** Every existing receiver of
   `TASK_ASSIGN` / `TASK_PROGRESS` / `TASK_COMPLETE` keeps working
   without code changes. New fields are optional and omitted when
   unused.
2. **Cat-A enforcement.** Sub-agent count is bounded by
   `role.max_parallel_tasks`, gated by **A-019** in
   `regulatory_layer/category_a/constitutional_rhoai.rego`.
3. **Edge-friendly state.** Cluster identity is in-memory authoritative
   plus an *optional* Redis mirror — the runtime works on disconnected
   edge nodes that have no Redis at all.
4. **Operator-driven.** Markdown role authoring (PR #28), prompt-pane
   slash commands (PR #30), and a live cluster topology panel
   (PR #29) put the operator in direct control without leaving the
   TUI.

---

## High-level architecture

```
                            ┌──────────────────────────────────┐
   Operator types prompt ──►│ TUIPromptChannel                 │
       (PR #30 slash         │  send(prompt, target_role, …)    │
        commands route       │                                  │
        here too)            └──────────────┬───────────────────┘
                                            │  TASK_ASSIGN (single agent)
                                            ▼
                            ┌──────────────────────────────────┐
                            │ Arbiter — PlanExecutor (PR #27)   │
                            │  _maybe_build_cluster()           │
                            │   ├─ resolve role.estimator       │
                            │   ├─ derive_complexity(step)      │
                            │   ├─ build_estimator(role)(...)   │
                            │   ├─ A-019 clamp + register       │
                            │   └─ dispatch N TASK_ASSIGNs       │
                            │       (cluster_id, target_agent_id)│
                            └──────────────┬───────────────────┘
                                            │ N × TASK_ASSIGN with cluster_id
                            ┌───────────────┼───────────────┐
                            ▼               ▼               ▼
                       agent-1         agent-2         agent-3
                            │               │               │
            TASK_PROGRESS   │               │               │  TASK_PROGRESS
            (cluster_id     │               │               │  (cluster_id
             echoed back)   │               │               │   echoed back)
                            ▼               ▼               ▼
                            ▼ TASK_COMPLETE  ▼ TASK_COMPLETE ▼ TASK_COMPLETE
                            │               │               │
                            └─────┬─────────┴───────────────┘
                                  │ aggregation
                                  ▼
                       ┌──────────────────┐
                       │ Arbiter folds    │
                       │ N completions    │
                       │ into one step    │
                       │ COMPLETE / FAILED│
                       └──────────────────┘
                                  │
                                  ▼
                       ┌──────────────────┐
                       │ TUI Cluster      │
                       │ Panel (PR #29)   │
                       │  + Prompt pane   │
                       │  transcript      │
                       └──────────────────┘
```

---

## PR #26 — `cluster_id` propagation foundation

**File added:** `acc/cluster.py`

### `ClusterPlan` dataclass

A compact wire-friendly description returned by the estimator and
echoed onto every per-member signal:

```python
@dataclass
class ClusterPlan:
    cluster_id: str          # "c-<32 hex>" — discriminates from task_id / agent_id
    target_role: str         # role every member is instantiated with
    subagent_count: int      # always >= 1; checked in __post_init__
    parent_task_id: str      # the originating PLAN step's task_id
    skill_mix: list[str]     # flat skill list; arbiter slices across members
    estimated_difficulty: float = 0.0   # 0..1 normalised; UI tint
    reason: str = ""                    # human-readable explanation
    created_at: float        # wall clock; drives 30 s grace window
```

`new_cluster_id()` emits `c-<uuid4 hex>` so log lines distinguish
cluster ids from task ids (`plan-…`, `t-…`) and agent ids
(`<role>-<hex>`) at a glance.

### Registry

Module-globals + `threading.Lock`. Hot-path callers (arbiter
dispatch, agent task loop, TUI fan-in) all hit the same in-memory
view; passing a registry instance through every signature would have
ballooned the call sites without buying isolation.

```python
register_cluster(plan)        # synchronous; mirrors to Redis fire-and-forget
lookup_cluster(cluster_id)    # local cache; returns None on miss
fetch_cluster_async(cid)      # consults Redis on local-cache miss
unregister_cluster(cid)       # arbiter calls after all members complete
list_clusters()               # snapshot for TUI startup backfill
configure_redis_mirror(client, key_prefix=…, ttl_s=…)
_reset_for_tests()            # fixture teardown only
```

Redis mirroring is *best effort*. When Redis is unreachable
(disconnected edge), the in-memory registry still works for the local
process. This matches ACC's offline-first edge guarantee.

### Wire-protocol propagation

Three call sites changed, each strictly back-compatible (the new
fields are omitted from outbound payloads when unset):

| File | Change |
|---|---|
| `acc/plan.py:_publish_task_assign` | New `cluster_id` and `target_agent_id` keyword args. |
| `acc/agent.py:_handle_task` | Extracts `inbound_cluster_id` from TASK_ASSIGN; echoes it on every outbound TASK_PROGRESS and TASK_COMPLETE. |
| `acc/tui/client.py:NATSObserver` | New `register_cluster_listener(cluster_id, callback)` + `_fan_out_cluster(data)` with per-callback exception isolation. |

### Tests — `tests/test_cluster_propagation.py`, 21 cases

* Dataclass invariants (subagent_count ≥ 1, difficulty in [0, 1]).
* `to_dict / from_dict` round-trip preserves every field.
* `from_dict` tolerates missing optional fields (forward-compat).
* In-memory registry round-trip; idempotent unregister; list-snapshot
  is mutation-safe.
* `fetch_cluster_async` cache-hit + cache-miss-no-Redis behaviour.
* `_publish_task_assign` omits cluster_id when not supplied; attaches
  it (and target_agent_id) verbatim when supplied; treats empty
  string as "not supplied".
* Cluster listener fires on TASK_PROGRESS + TASK_COMPLETE with
  `cluster_id`; silent on payloads without it; unregister drops all
  callbacks; multiple listeners each fire; per-callback exception
  isolation.

---

## PR #27 — Estimator + sub-cluster spawn

**File added:** `acc/estimator.py`

### `TaskComplexity` input + `Estimator` Protocol

```python
@dataclass
class TaskComplexity:
    expected_tokens: int
    task_type: str = ""
    required_skills: list[str] | None = None
    has_external_io: bool = False

@runtime_checkable
class Estimator(Protocol):
    def __call__(
        self,
        task: TaskComplexity,
        role_config: Any,            # acc.config.RoleDefinitionConfig
        available_skills: list[str],
        *,
        parent_task_id: str = "",
    ) -> ClusterPlan: ...
```

Pure callable on purpose — easy to fuzz, easy to write a custom one,
no async required.

### `default_estimator` heuristic

```
n   = base + ceil(expected_tokens / per_n_tokens)
n  += sum(bump for kw, bump in difficulty_signals if kw in (task_type lower))
n   = max(1, min(n, cap, role.max_parallel_tasks))
```

* `base` defaults to 1, `per_n_tokens` to 2000, `cap` to 5.
* `difficulty_signals` are operator-supplied keyword bumps, e.g.
  `{"keyword": "security", "bump": 1}`.
* `estimated_difficulty = min(1.0, total_bumps / 4)` is purely a UI
  signal — clusters that hit four or more difficulty signals saturate
  the colour scale in the cluster panel.

### Strategy dispatch — `build_estimator`

| Strategy | Behaviour |
|---|---|
| `heuristic` (default) | The function above. |
| `fixed` | Always emit `role.estimator['fixed']['count']` clamped to `max_parallel_tasks`. |
| `module:<dotted.path>` | `importlib.import_module` + `getattr`; expected to be callable with the same signature. |

Unknown strategy or import failure → log a warning + fall back to
`default_estimator`. The arbiter NEVER crashes on a buggy
operator-supplied estimator.

### `slice_skill_mix` round-robin

The arbiter does not pre-shard skills inside the estimator. After the
estimator returns N, the arbiter calls `slice_skill_mix(skill_mix, n)`
to round-robin assign skills across members so no one sub-agent
loads every skill prompt.

### `derive_complexity` from a raw step

Best-effort conversion of an inbound PLAN step to `TaskComplexity`.
4-bytes-per-token approximation; `[SKILL: <name>]` markers extracted
via regex. Off by ~2× is fine — it shifts the estimate by at most one
sub-agent.

### `RoleDefinitionConfig` schema additions

```python
max_parallel_tasks: int = 1
estimator: dict[str, Any] = Field(default_factory=dict)
```

`estimator` is a free-form dict on purpose: `module:` strategies do
not need schema bumps in `acc/config.py`.

### `PlanExecutor` changes — `acc/plan.py`

```python
def __init__(
    self,
    collective_id: str,
    publish: PublishFn,
    arbiter_id: str,
    max_active_plans: int = 16,
    *,
    role_resolver: Callable[[str], Optional[Any]] | None = None,
    skill_resolver: Callable[[str], list[str]] | None = None,
) -> None: ...
```

`role_resolver` / `skill_resolver` are optional. When *unset* (legacy
/ tests), dispatch is byte-identical to PR #26 — one `TASK_ASSIGN`
per step, no `cluster_id`, no aggregation.

When **set**, every ready step runs through `_maybe_build_cluster`:

1. Resolve the role.
2. `derive_complexity(step.raw)` → `TaskComplexity`.
3. `build_estimator(role)` → callable.
4. Call it. **A-019 clamp** runs in-process here as defence in depth.
5. If `subagent_count == 1`, fall back to single-agent dispatch
   (no cluster bookkeeping).
6. Otherwise: `register_cluster(plan)`, then `_dispatch_cluster()`
   which publishes N `TASK_ASSIGN`s sharing one `cluster_id`.

### Aggregation — `on_task_complete`

When a step is part of a cluster (`_step_clusters[(plan_id,
step_id)]` is set), each TASK_COMPLETE increments
`members_done`. The step transitions only when *all* members report:

* COMPLETE if every member returned `blocked=False`.
* FAILED if any one member was blocked (cascades to dependents via
  the existing `_cascade_failure`).

Cluster auto-unregistered on transition.

### Cat-A A-019

`regulatory_layer/category_a/constitutional_rhoai.rego` bumped
0.4.0 → 0.5.0 with two new rules:

```rego
deny_cluster_oversize if {
    input.action == "CLUSTER_SPAWN"
    input.subagent_count > input.agent.max_parallel_tasks
}

deny_cluster_nonpositive if {
    input.action == "CLUSTER_SPAWN"
    input.subagent_count < 1
}
```

External (Gatekeeper) admission enforces the same invariant as the
in-process clamp — defence in depth.

### Tests — `tests/test_estimator.py`, 26 cases

Pure-function: heuristic shape, role-cap clamp, [0,1] difficulty
bound, skill-mix precedence (required → defaults → all-available),
`fixed` strategy, `module:` import + import-failure fallback,
unknown-strategy fallback, slice round-robin, derive_complexity
SKILL-hint extraction.

`PlanExecutor` integration: single-agent fallback when no resolver,
fan-out wire shape (3 TASK_ASSIGNs sharing one cluster_id),
fallback-to-single when estimator returns 1, **A-019 clamp**,
estimator-failure → single-agent fallback, all-members-report
aggregation, any-blocked-fails-step.

---

## PR #28 — Markdown role authoring

**File added:** `acc/role_md.py`

### Source format

```markdown
# Role: coding_agent
Version: 1.2.0
Persona: analytical
Domain: software_engineering
Receptors: software_engineering, security_audit

## Purpose
Generate, review, and test code artefacts.

## Task Types
- CODE_GENERATE
- CODE_REVIEW

## Allowed Actions
- read_vector_db

## Category-B Setpoints
- token_budget: 4096

## Capabilities
- Allowed skills: echo, code_review
- Default skills: echo
- Max skill risk: MEDIUM
- Allowed MCPs: echo_server
- Max parallel tasks: 3

## Sub-cluster Estimator
Strategy: heuristic
Base: 1
Per-N-tokens: 2000
Skill-per-subagent: 2
Cap: 5
Difficulty signals:
- security → +1
- concurrency → +2

## System Prompt
You are a precise software engineer.
…
```

### Public API

```python
compile_markdown(source) -> _CompileResult
decompile_to_markdown(role_yaml_dict, *, role_name, system_prompt='', extras=None) -> str
lint_markdown(source) -> list[str]                # exhaustive diagnostics
compile_file(md_path, dest_dir=None) -> (yaml_path, system_prompt_path)
decompile_dir(role_dir) -> str
```

### CLI subcommands

```
acc-cli role compile <path/to/role.md> [--dest <dir>]
acc-cli role lint <path/to/role.md>
acc-cli role decompile <name>
```

Exit codes are conventional: 0 clean, 1 dirty/missing/invalid.

### Round-trip identity

The bundled `roles/coding_agent` role is the integration test:
`compile(decompile(yaml)) == compile(yaml)` structurally. The
required-section guard raises `RoleMarkdownError` with a 1-indexed
line number so `acc-cli role lint` can point operators at the exact
fix.

### Edge case handled

The System Prompt section is **terminal**. Inner `## H2` lines
inside the prompt body — common when system prompts include their
own headings — would otherwise be misread as new outer sections and
clobber Task Types / Allowed Actions. The parser flips a flag once
it enters System Prompt and treats everything after verbatim.

### Tests — `tests/test_role_markdown.py`, 18 cases

Synthetic full-section coverage, required-section enforcement, risk
level rejection, round-trip identity for synthetic + bundled
coding_agent role, file-write round-trip, unknown-section preserved
on extras carry-over (forward-compat).

---

## PR #29 — TUI cluster topology panel

**Files added:** `acc/tui/widgets/cluster_panel.py`,
`tests/test_cluster_panel.py`.

### Panel design

`ClusterPanel` is a `Static` subclass mounted between the prompt
target row and the transcript. Header shows
`Clusters: N (Σ M agents)`. Body, when expanded, lists:

```
▼ Clusters: 1 (Σ 3 agents)
  c-abc12345 · coding_agent · 3 agents · fixed strategy, count=3
    ● coding_agent-a · skill:code_review   · step 2/4 · running
    ● coding_agent-b · skill:test_genera   · step 3/4 · running
    ● coding_agent-c · skill:echo          · step 4/4 · complete
```

Status colour-coded: yellow `running`, green `complete`, red
`blocked`. Header chevron toggles `▶ ↔ ▼`.

### Why no reactive watcher

Textual ≥ 0.80 fires reactive watchers from inside the layout pass on
some paths. `Static.update()` re-enters layout, and the watcher
firing again recurses with a `None` renderable — Pilot tests trip
`'NoneType' object has no attribute 'get_height'`. The panel
exposes `render_now()` and the screen drives it explicitly from
its `watch_snapshot` (a non-layout context).

### `_render_panel` not `_render`

Textual's `Widget._render` is an internal method. The panel's render
helper had to be renamed `_render_panel` to avoid silently shadowing
that internal — that bug manifested as a recursion in the layout
pass.

### Snapshot aggregation — `acc/tui/client.py`

`NATSObserver._fan_out_cluster` calls `_update_cluster_topology`
which folds every cluster-tagged TASK_PROGRESS / TASK_COMPLETE into
`CollectiveSnapshot.cluster_topology`:

```python
{
  cluster_id: {
    "cluster_id": str, "target_role": str,
    "subagent_count": int,    # running max of witnessed members
    "members": {
      agent_id: {
        "task_id": str, "step_label": str,
        "current_step": int, "total_steps": int,
        "status": "running" | "complete" | "blocked",
        "skill_in_use": str,           # parsed from step_label
        "last_seen": float,
      }
    },
    "created_at": float,
    "finished_at": float | None,
    "reason": str,
  }
}
```

`skill_in_use` is parsed heuristically from labels like
`"Calling skill:<name>"` and `"Calling mcp:<server>.<tool>"` — the
convention `acc/capability_dispatch.py` emits in PR #20.

### 30-second grace window

Once every member has reported, `finished_at` is stamped. The panel
keeps the row visible for 30 s after that so the operator can read
the final state before it disappears.

### Tests — `tests/test_cluster_panel.py`, 11 cases

Aggregator: progress event creates row + member, skill extraction
for `skill:` and `mcp:`, subagent_count grows as members appear,
TASK_COMPLETE marks member done + stamps finished_at, blocked
member marked blocked, payloads without cluster_id do not pollute
topology. Panel render: collapsed header counts, expanded member
rows, grace-window filter drops finished, recently-finished kept
visible.

---

## PR #30 — Prompt-pane slash commands

**Files added:** `acc/slash_commands.py`,
`tests/test_slash_commands.py`. **Subject helper added:**
`subject_task_cancel` in `acc/signals.py`.

### Parser surface

Pure function `parse(text) -> SlashIntent`. Verbs:

| Verb | Routing kind | Effect |
|---|---|---|
| `/help`, `/` | `KIND_HELP` | Render `HELP_TEXT` in transcript. |
| `/cancel <task_or_cluster_id>` | `KIND_CANCEL` or `KIND_CLUSTER_KILL` | Auto-routes by `c-` prefix. Publishes TASK_CANCEL. |
| `/cluster show [<cid>]` | `KIND_CLUSTER_SHOW` | Render snapshot in transcript (no NATS). |
| `/cluster kill <cid>` | `KIND_CLUSTER_KILL` | Publish TASK_CANCEL with cluster_id. |
| `/role list` | `KIND_ROLE_LIST` | List local roles in transcript. |
| `/skills` | `KIND_SKILLS` | List allowed/default skills for current target role. |
| `/oversight pending\|approve\|reject` | three kinds | Stub today; full wiring follows. |

Unknown verb → `KIND_UNKNOWN` with a helpful message that includes
`/help`. Missing required arg → `KIND_INVALID` with the usage line.
**The parser never raises.**

Non-slash input → `KIND_NOT_SLASH`. The screen continues with the
legacy LLM-dispatch path. Empty / whitespace-only is also
`KIND_NOT_SLASH`. **Full back-compat.**

### Wire — `SIG_TASK_CANCEL`

```
SIG_TASK_CANCEL = "TASK_CANCEL"
subject_task_cancel(cid) -> "acc.{cid}.task.cancel"
```

Subject is distinct from `subject_task` so a flood of operator
cancels never starves the main task subject and so cancel handlers
can subscribe cheaply.

Payload schema:

```json
{
  "signal_type": "TASK_CANCEL",
  "collective_id": "<cid>",
  "ts": 1.700e9,
  "task_id": "<optional>",
  "cluster_id": "<optional, takes precedence over task_id>"
}
```

### Screen-side dispatch

`acc/tui/screens/prompt.py:action_send` branches on `/`:

```python
if prompt.startswith("/"):
    self._dispatch_slash(prompt)
    self.query_one("#prompt-textarea", TextArea).clear()
    return
```

`_dispatch_slash` switches on `intent.kind` and calls the
appropriate side-effect helper (`_publish_cancel`,
`_render_cluster_show`, `_render_role_list`,
`_render_skills_summary`). The transcript records every interaction
via the existing `_append_history` so the operator sees a single
chronological log of prompts + responses + slash interactions.

### Tests — `tests/test_slash_commands.py`, 22 cases

Pass-through (empty / non-slash), every verb routed correctly,
`c-` prefix routing on `/cancel`, required-arg enforcement, unknown
verb helpful message, `HELP_TEXT` covers every accepted verb (so
new verbs cannot silently lack help), wire subject format pinned.

### Out of scope (deferred)

* Agent-side TASK_CANCEL subscriber + cooperative checkpoint inside
  `CognitiveCore.process_task` — the *cancellation* itself. PR #30
  ships only the operator surface; the agent-side cancel lands in a
  small follow-up to keep PR #30 focused.
* Full `/oversight` wiring. Parser accepts the verbs; screen-side
  stubs them with a "use Compliance screen" hint.

---

## File map

| Module | Role | Introduced in |
|---|---|---|
| `acc/cluster.py` | ClusterPlan dataclass + registry | PR #26 |
| `acc/estimator.py` | Estimator protocol + heuristic + strategy dispatch | PR #27 |
| `acc/role_md.py` | Markdown ↔ canonical role.yaml compiler | PR #28 |
| `acc/slash_commands.py` | Pure-function operator slash parser | PR #30 |
| `acc/tui/widgets/cluster_panel.py` | Cluster topology widget | PR #29 |
| `acc/plan.py` | Cluster fan-out + aggregation in PlanExecutor | PR #26 + PR #27 |
| `acc/agent.py` | cluster_id propagation in `_handle_task` | PR #26 |
| `acc/tui/client.py` | `_fan_out_cluster` + `_update_cluster_topology` | PR #26 + PR #29 |
| `acc/tui/models.py` | `CollectiveSnapshot.cluster_topology` field | PR #29 |
| `acc/tui/screens/prompt.py` | Mount cluster panel + slash dispatch | PR #29 + PR #30 |
| `acc/cli/role_cmd.py` | `role compile/decompile/lint` subcommands | PR #28 |
| `acc/signals.py` | `SIG_TASK_CANCEL` + `subject_task_cancel` | PR #30 |
| `acc/config.py` | `max_parallel_tasks` + `estimator` on RoleDefinitionConfig | PR #27 |
| `regulatory_layer/category_a/constitutional_rhoai.rego` | A-019 (0.5.0) | PR #27 |

Test modules added: `tests/test_cluster_propagation.py` (21),
`tests/test_estimator.py` (26),
`tests/test_role_markdown.py` (18),
`tests/test_cluster_panel.py` (11),
`tests/test_slash_commands.py` (22). Total **98 new tests**.

---

## Test matrix (local Windows env, Python 3.14, no acc1)

| Suite | Result |
|---|---|
| `tests/test_cluster_propagation.py` | 21/21 |
| `tests/test_estimator.py` | 26/26 |
| `tests/test_role_markdown.py` | 18/18 |
| `tests/test_cluster_panel.py` | 11/11 |
| `tests/test_slash_commands.py` | 22/22 |
| Wider spot-check across `target_agent_id`, `task_progress_*`, `coding_agent_role/negative_paths`, `progress_confidence_trend`, `prompt_screen_pilot` | **121/121** |

Pre-existing 60 TUI failures (`test_tui_smoke`, `test_tui_screens`,
`test_tui_client` — Textual API drift, unrelated to this work) are
the only failures and are present on `main` already.

---

## Wire-protocol summary (cheat sheet)

### Outbound TASK_ASSIGN (arbiter → cluster member)

```json
{
  "signal_type": "TASK_ASSIGN",
  "task_id": "plan-<plan>-<step>-<cid8>-m<i>",
  "plan_id": "<plan_id>",
  "step_id": "<step_id>",
  "collective_id": "<cid>",
  "from_agent": "<arbiter_id>",
  "target_role": "coding_agent",
  "task_type": "CODE_REVIEW",
  "task_description": "...",
  "ts": 1.700e9,

  // Cluster fields (optional — present iff PlanExecutor is fanning out)
  "cluster_id": "c-<32 hex>",
  "target_agent_id": "coding_agent-<...>"
}
```

### Outbound TASK_PROGRESS (member → bus)

```json
{
  "signal_type": "TASK_PROGRESS",
  "task_id": "<echoed from TASK_ASSIGN>",
  "agent_id": "<this member>",
  "collective_id": "<cid>",
  "ts": 1.700e9,
  "progress": {
    "current_step": 3,
    "total_steps_estimated": 6,
    "step_label": "Calling skill:code_review",
    "confidence": 0.62,
    "confidence_trend": "RISING"
  },
  "cluster_id": "<echoed when present in inbound TASK_ASSIGN>"
}
```

### Outbound TASK_COMPLETE (member → bus)

```json
{
  "signal_type": "TASK_COMPLETE",
  "task_id": "...",
  "agent_id": "...",
  "collective_id": "<cid>",
  "ts": 1.700e9,
  "blocked": false,
  "block_reason": "",
  "latency_ms": 1234.5,
  "output": "<truncated>",
  "invocations": [{"kind": "skill", "target": "code_review", "ok": true, "error": ""}],
  "cluster_id": "<echoed when present in inbound TASK_ASSIGN>"
}
```

### Outbound TASK_CANCEL (operator → cluster)

```json
{
  "signal_type": "TASK_CANCEL",
  "collective_id": "<cid>",
  "ts": 1.700e9,
  "cluster_id": "c-...",   // OR task_id — see semantics
  "task_id": "..."
}
```

If `cluster_id` is present, every member of the cluster aborts. If
only `task_id` is present, that single task aborts.

---

## Forwarding compatibility

* Receivers that don't know about `cluster_id` simply do not see the
  field — payloads are JSON, missing key reads as `None` / absent in
  every consumer.
* The `estimator` block is a free-form dict — operators can add fields
  for custom strategies without bumping `RoleDefinitionConfig`.
* The cluster topology snapshot is itself a dict-of-dicts, never a
  pydantic model — tests synthesise it directly without re-importing
  schema.
* `acc/role_md.py` preserves unknown sections via the `extras`
  dict — markdown sections this version doesn't recognise are
  emitted verbatim on `decompile`, so a future tool that adds a
  section never loses data on round-trip.

---

## Known gaps (filed as follow-ups in PR #30 and PLAN doc)

1. **Agent-side TASK_CANCEL handler** — the operator-side publish ships
   in PR #30; the agent-side cooperative checkpoint inside
   `CognitiveCore.process_task` follows.
2. **Cluster autoscaling mid-task** — first version commits to the
   estimated count at PLAN-step start. Growing/shrinking under load
   is a separate orchestration enhancement.
3. **Mixed-role clusters** — current estimator returns one role per
   cluster. Heterogeneous clusters (e.g. one reviewer + two
   implementers in the same cluster) are deferred.
4. **LLM-decided estimator strategy** — the strategy hook
   (`module:`) exists but no reference LLM-driven estimator ships
   today.
5. **Cluster panel detail panel** — `/cluster show` renders a textual
   summary in the transcript; a dedicated SubtraceModal that opens
   the per-member step-by-step trace was deferred from PR #29 to
   keep its scope small.
