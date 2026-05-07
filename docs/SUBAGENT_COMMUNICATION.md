# Sub-agent Communication Patterns

**Companion to:** `IMPLEMENTATION_subagent_clustering.md` (the
implementation reference) and `PLAN_subagent_clustering.md` (the
design rationale).

This document describes **how cluster members talk to each other and
to the rest of the collective** under the wire protocol that
PR #26–#30 established. It is the contract sub-agent role authors,
skill implementers, and external integrators read before composing
their own clusters.

---

## Design principles

1. **No new bus subjects per cluster.** Members publish on the same
   per-collective subjects every other agent uses. The `cluster_id`
   tag is the *correlation key*, not a routing prefix.
2. **No direct member-to-member channel.** Cluster members do not
   talk to each other directly. All coordination is mediated by the
   arbiter or by shared state (scratchpad, vector store, working
   memory).
3. **Receptor-filtered broadcast** keeps the wire shape small.
   Members publish PARACRINE signals (`KNOWLEDGE_SHARE`,
   `TASK_PROGRESS`); only matching domain receptors react.
4. **Aggregation lives in the arbiter.** Consumers (TUI, audit log,
   downstream PLAN steps) never have to pre-compute "is the cluster
   done" — the arbiter publishes the resolved step transition.
5. **Cancellation is cooperative.** A `TASK_CANCEL` for the cluster
   is a hint, not a kill signal. Members check a cancel flag at
   each cognitive step boundary and exit cleanly with
   `blocked=True, block_reason="cancelled"`.

The biological framing: every cluster member is a *cell* in a
*tissue*. Cells in a tissue do not phone each other; they release
ligands into shared extracellular space (the bus) and listen on
their receptors (`domain_receptors`). The tissue organiser (arbiter)
reads the tissue state and decides next.

---

## Communication channels at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│  acc.{cid}.task                                                  │
│   - TASK_ASSIGN  (arbiter → member, carries cluster_id)          │
│   - TASK_COMPLETE(member → bus, echoes cluster_id)               │
│                                                                  │
│  acc.{cid}.task.progress                                         │
│   - TASK_PROGRESS (member → bus, echoes cluster_id)              │
│                                                                  │
│  acc.{cid}.task.cancel                                           │
│   - TASK_CANCEL  (operator → bus, by task_id or cluster_id)      │
│                                                                  │
│  acc.{cid}.knowledge.{tag}                                       │
│   - KNOWLEDGE_SHARE (member → bus, receptor-filtered)            │
│                                                                  │
│  acc.{cid}.eval.{task_id}                                        │
│   - EVAL_OUTCOME    (member → self, autocrine)                   │
│                                                                  │
│  acc.{cid}.alert                                                 │
│   - ALERT_ESCALATE  (member → bus, endocrine — Cat-A blocks)     │
│                                                                  │
│  acc.{cid}.plan.{plan_id}                                        │
│   - PLAN re-broadcast (arbiter only, A-012)                      │
│                                                                  │
│  Shared state (out-of-band of NATS):                             │
│   - Redis scratchpad: per-task, per-cluster, per-collective keys │
│   - LanceDB vector store: episode + role centroid                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pattern A — Static decomposition (the default today)

Used by the default `heuristic` and `fixed` estimator strategies.
The arbiter splits the workload **at PLAN-step expansion**, before
any member starts.

```
                       arbiter
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         member 1    member 2    member 3
        (skill A)   (skill B)   (skills C+D)
              │           │           │
              ▼           ▼           ▼
                   TASK_COMPLETE × 3
                          │
                          ▼
                       arbiter
                  fold + transition step
```

Properties:
* No mid-task coordination needed; members are independent.
* Output integration happens at the arbiter, post-hoc.
* The aggregator strategy is "concatenate outputs, COMPLETE if all
  ok, FAILED if any blocked" — the simplest sensible default.

When to use:
* Tasks that decompose cleanly along skill lines (review +
  implementation + tests, written in parallel).
* Reproducibility-sensitive runs — the static split is
  deterministic given the same task description.
* Edge deployments where members may have intermittent connectivity.

When **not** to use:
* Tasks where one member's output is required input for another.
  Use Pattern B instead, or split into multiple PLAN steps with
  `depends_on` edges (the existing PLAN executor handles that).

---

## Pattern B — Knowledge-share fan-in (early signal)

A member that finishes early publishes a `KNOWLEDGE_SHARE` on a
shared tag. Other members of the same cluster **may** subscribe to
that tag (via `domain_receptors`) and incorporate the early signal
into their ongoing work.

```
member 1 finishes step 4 ──► KNOWLEDGE_SHARE
                               (tag: code_patterns)
                                       │
                                       ▼  paracrine (receptor-filtered)
                              ┌────────┴────────┐
                              ▼                 ▼
                          member 2          member 3
                          (continues)       (continues)
```

Wire shape:

```json
{
  "signal_type": "KNOWLEDGE_SHARE",
  "agent_id": "coding_agent-aaa",
  "collective_id": "<cid>",
  "ts": 1.700e9,
  "domain_tag": "code_patterns",
  "knowledge_type": "draft_interface",
  "content": "...",
  "confidence": 0.7,
  "cluster_id": "<optional, but recommended>"
}
```

Notes:
* `cluster_id` echo on KNOWLEDGE_SHARE is **strongly recommended**
  but not enforced — peer members can subscribe by domain_tag alone
  if cross-cluster knowledge propagation is desired.
* Receivers MUST be idempotent: paracrine broadcast may arrive
  multiple times, and members may also see knowledge from prior
  clusters that finished moments ago.

When to use:
* Long-running tasks where a fast member's intermediate output
  unblocks the slow one (a "draft interface" leaks to the
  implementer; a "test scaffold" leaks to the reviewer).

Anti-pattern:
* Using KNOWLEDGE_SHARE as a request/response channel (member 2
  asks member 1 a question). The PARACRINE mode has no reply
  guarantee. For tightly coupled coordination, split into separate
  PLAN steps with `depends_on` edges.

---

## Pattern C — Scratchpad rendezvous (out of NATS)

For larger artefacts (full file contents, vector embeddings,
intermediate datasets), members write to a shared **scratchpad
namespace** keyed by `cluster_id`. The Redis layout:

```
acc:<cid>:cluster:<cluster_id>:<key>     # cluster-scoped
acc:<cid>:task:<task_id>:<key>            # task-scoped
acc:<cid>:agent:<agent_id>:<key>          # agent-scoped
```

A member that wants to publish a draft writes to the cluster-scoped
key and emits a `KNOWLEDGE_SHARE` whose `content` field carries
*just the key path*, not the artefact. Receivers fetch via the
existing scratchpad client.

```python
scratchpad.set(
    f"acc:{cid}:cluster:{cluster_id}:draft_interface",
    draft_text,
    ttl_s=900,           # cleared after 15 min — clusters are ephemeral
)
publish_knowledge_share(
    domain_tag="code_patterns",
    knowledge_type="draft_interface_ref",
    content=f"acc:{cid}:cluster:{cluster_id}:draft_interface",
    cluster_id=cluster_id,
)
```

When to use:
* Artefacts > a few KB that would balloon the bus payload.
* Anything that another member needs to *read repeatedly* during
  its own work — keeping the canonical copy in Redis means there
  is no race on bus delivery order.

---

## Pattern D — Autocrine self-feedback

A member emits an `EVAL_OUTCOME` for its own work — the existing
ACC-10 self-evaluation mechanism. Cluster-tagged EVAL_OUTCOMEs let
the arbiter compare members' self-scores when ranking outputs.

```json
{
  "signal_type": "EVAL_OUTCOME",
  "agent_id": "coding_agent-aaa",
  "collective_id": "<cid>",
  "ts": 1.700e9,
  "task_id": "<member task_id>",
  "cluster_id": "c-...",
  "criteria_scores": {
    "correctness": 0.85, "style": 0.9, "security": 1.0
  },
  "overall_score": 0.91,
  "verdict": "GOOD"
}
```

The arbiter does not currently weight cluster member outputs by
self-score (the aggregator concatenates), but the data is on the
bus and persisted to the episode log so future enhancements can
prefer the highest-scoring member.

---

## Pattern E — Cancel propagation

Operator publishes `TASK_CANCEL` with a `cluster_id`:

```json
{
  "signal_type": "TASK_CANCEL",
  "collective_id": "<cid>",
  "cluster_id": "c-abc",
  "ts": 1.700e9
}
```

The agent-side handler subscribes to `acc.{cid}.task.cancel` and:

1. Looks up its current `cluster_id` (set when it received its
   `TASK_ASSIGN` in `_handle_task`).
2. If they match, sets a `cancel_event` (asyncio.Event) that
   `CognitiveCore.process_task` checks at every step boundary.
3. Pipeline raises `asyncio.CancelledError` cleanly; persistence is
   skipped; TASK_COMPLETE is emitted with `blocked=True,
   block_reason="cancelled"`.

> **As of PR #30 the operator-side publish is in place but the
> agent-side handler is the next follow-up. Until that lands the
> cancel signal is observable on the bus but members do not stop.**

---

## Anti-patterns

| Don't | Why | Do instead |
|---|---|---|
| Subscribe one member to another member's task subject | Members are not addressable per task; only by `agent_id`. | Use `KNOWLEDGE_SHARE` (paracrine) or scratchpad rendezvous. |
| Use `TASK_ASSIGN` to inject mid-cluster work | A-012 + A-019: only the arbiter publishes TASK_ASSIGN; cluster size is fixed at PLAN-step start. | Submit a follow-on PLAN with `depends_on`. |
| Block on a future for another member's output | Synchronous waits don't tolerate bus delivery delays. | Express dependency as a separate PLAN step. |
| Use the cluster bus for large artefacts | Bus payloads are msgpack-wrapped; large blobs starve other channels. | Scratchpad rendezvous (Pattern C). |
| Skip echoing `cluster_id` on outbound signals | TUI cluster panel cannot aggregate without it; cancel cannot reach the right members. | Always echo when present in inbound TASK_ASSIGN (PR #26 already does this). |
| Treat `EVAL_OUTCOME` as authoritative | It's self-graded; the arbiter does not currently weight by it. | Use it for episode learning + Cat-C rule promotion only. |

---

## Failure modes + recovery

| Failure | Wire signal | Operator-visible effect | Mitigation |
|---|---|---|---|
| One member blocked by Cat-A (e.g. A-017 forbidden skill) | TASK_COMPLETE blocked=True | Cluster step FAILED; cascades to dependents. | A-017 ceiling check at role authoring time; lint via `acc-cli role lint`. |
| One member crashes / loses connection | TASK_COMPLETE never arrives for that member | Cluster step stuck RUNNING. Currently no auto-timeout. | Operator `/cluster kill <cid>` or `/cancel <task_id>`. |
| Cluster oversize from custom estimator | TASK_ASSIGN flood with cluster_id | A-019 in-process clamp + Gatekeeper rejection. | Defence-in-depth — no operator action needed. |
| Late TASK_COMPLETE after eviction | task_id no longer in `_task_index` | Silently dropped at arbiter. | None — late completions are harmless. |
| Cluster panel shows phantom row | Some payload had cluster_id but no real cluster registered | Panel auto-evicts after 30 s grace. | None — cosmetic only. |

---

## Identity + naming conventions

* **`cluster_id`** — `c-<32 hex>`. Never re-used. Generated by
  `acc.cluster.new_cluster_id`.
* **Member task_id** — `plan-<plan_id>-<step_id>-<cid8>-m<i>` where
  `cid8` is the first 8 chars of `cluster_id` and `i` is the
  1-indexed member ordinal. Lets log readers identify cluster
  members at a glance without consulting the registry.
* **Member agent_id** — unchanged; whatever role spawn produced.
  Members of one cluster have different `agent_id`s but the same
  `cluster_id`.
* **Skill prefix in step labels** — `Calling skill:<name>` or
  `Calling mcp:<server>.<tool>`. PR #29's TUI panel parses this to
  populate the per-member `skill_in_use` column. New skill
  invokers SHOULD emit step labels in this exact form.

---

## Practical advice for sub-agent role authors

* **Default to Pattern A.** Static decomposition covers ~80% of
  useful clusters and has the simplest mental model.
* **Reach for Pattern B when one member is the natural "lead".**
  E.g. an interface designer whose draft unblocks two implementers.
  Always echo `cluster_id` on the KNOWLEDGE_SHARE so the receivers
  can filter to their own cluster.
* **Reach for Pattern C for blob payloads.** Anything > 4 KB is
  better off in the scratchpad with a key reference shared via
  KNOWLEDGE_SHARE.
* **Use `domain_receptors` deliberately.** A member that only ever
  listens to its own role's KNOWLEDGE_SHAREs should narrow its
  receptors to that domain — the bus stays cheap and unrelated
  ligands don't burn the member's context window.
* **Always check `target_agent_id` filter implicit behaviour.**
  If you supply `target_agent_id` on a TASK_ASSIGN, only that
  specific agent processes it (PR #12 wire). Cluster fan-out can
  use this when the estimator wants to pin individual members to
  named agents instead of the broadcast-by-role default.

---

## Forward-compat notes for skill / channel authors

* The cluster topology snapshot in `CollectiveSnapshot.cluster_topology`
  is a free-form dict on purpose. Future analytics tools can add
  fields without changing the schema.
* The estimator block is a free-form dict on purpose. New strategy
  names + heuristic knobs do not require pydantic schema bumps.
* `slash_commands.HELP_TEXT` includes every accepted verb. New verbs
  added without updating `HELP_TEXT` will fail
  `tests/test_slash_commands.py::test_help_text_contains_every_verb`.
