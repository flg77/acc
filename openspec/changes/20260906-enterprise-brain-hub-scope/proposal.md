# 20260906-enterprise-brain-hub-scope — proposal

## Why

HG-40.1 (vault, 2026-09-04) decomposed "many users, RBAC-separated personal
agents, all contributing to an enterprise brain, running for months" and found
that `20260823-attributed-memory` already *is* the brain's behaviour — private
notes per scope, publication only through a proposal a person approves,
quorum of two people, dissent, probation, erasure — with one limit: **it lives
inside a collective**. With instances (`20260906-acc-instance`, v0.13.0) each
person's collective has its own private and shared tiers and there is no
scope one level up that all of them can publish *into* and read *from*.

What the tree said on 2026-09-06 (verified while building this; one HG-40.1
claim was optimistic):

* Notes are consolidated per role from episodes and cached in Redis per
  collective / role / scope (`acc:<cid>:memory_notes:<role>:<scope>`); a
  published copy lives under the destination
  (`acc:<cid>:memory_notes_shared:<role>:<dest>`). The prompt path reads the
  two keys of its own collective. Redis is shared by every instance on a
  host by design — so a hub tier is a key space, not a new store.
* `_dispatch_publish` applies an approved publish proposal; **nothing at
  runtime builds one** — `build_publish_proposal` had no caller outside the
  tests. HG-40.1 said "the assistant carries it as a PUBLISH proposal"; the
  carrier existed, the proposer did not.
* The information rule ([2] of the memory change: a fragment is not retrieved
  below the ceiling of the task that produced it) was **not enforced** —
  notes carried no ceiling and readers were not compared to one. D-014 made
  it expressible.
* Hot-cache entries were bare summaries: nothing a surface could turn into a
  proposal (no note id, no people).
* `hub_collective_id` existed in the config and reached the A2A bridge and
  `peer_collectives`; instances pass it as `ACC_HUB_COLLECTIVE_ID`. Memory
  never used it.

## What

**A hub tier as a destination.** A publish proposal's destination may be
`hub:<hub collective id>`. On approval the note lands in the hub's
**enterprise tier** — `memory_notes_shared` under the *hub's* collective id,
destination `enterprise` — never in the publishing collective's own shared
tier. Everything the memory change built keeps applying: the quorum of two
people, the named approver, dissent, probation, the journal entry (now with
`destination_collective` and `ceiling`).

**A hub tier as the only cross-instance read path.** A collective bound to a
hub reads a third key on the prompt path: the hub's enterprise tier for its
role. It never reads another collective's shared tier, and the hub never
reads itself twice. Hub-only (HG-40.1 Q2) is the design: peer reads would
reintroduce "one person's habit becomes everyone's" without a quorum.

**The information rule, enforced.** A note carries the **highest** ceiling
among its source tasks (`note_ceiling`; an unattributed source — the
operator's own surface before v0.13.0, a plan step — counts as the operator's,
CRITICAL). The cache entry and every published copy carry it; the prompt-path
read skips any entry above the requester's ceiling
(`reader_ceiling = ceiling_of(task_payload)`, "" for the operator's own
work), at every boundary — own scope, shared tier, hub. A cache written
before ceilings reads as CRITICAL. Authority is a proxy for sensitivity here,
deliberately and imperfectly, as the memory change says.

**Proposing, by hand.** Hot-cache entries now carry the note id, the people
behind the note, its ceiling, scope and dissent, so a surface can propose from
the cache alone. `acc-cli memory propose --role R --scope S --index N --to
<scope|hub:<cid>>` builds the proposal (quorum enforced; `--override` marks a
single-source note) and queues it the way the assistant does — an oversight
row plus the cached payload the approval dispatcher loads — so it is
approved in Compliance, the Prompt pane or `acc-cli oversight approve`, and
lands through the same `_dispatch_publish`.

**The curator's look.** `acc/memory_curate.candidates(redis, hub)` is the
curator's job as a function: shared notes across the collectives on this
Redis that rest on the quorum, are past probation, and are not in the hub yet
(the hub's own keys are never candidates; `collectives=` limits to the bound
ones). `acc-cli memory curate --hub <cid> [--propose]` lists them and, on
request, queues one publish proposal per candidate in its home collective.
The curator never learns and never answers anyone; a role that runs this on
a schedule is Phase 2.

**Erasure reaches the hub.** `memory forget` pulls a deleted or demoted note
out of the hub's enterprise tier too (`hub_collective_id` threaded through
`forget_person`); the report names `hub:<cid>` among `unpublished_from`.

## Decisions

* **Hub-only cross-instance reads** (HG-40.1 Q2, proposed there; built so;
  awaiting the operator's confirmation).
* **θ stays per instance; the hub never learns** (HG-40.1 Q3): the policy
  layer is per role per collective and nothing here touches it. Recorded, not
  built.
* **The hub is a collective id.** Its enterprise tier is its shared tier
  under a fixed destination; a hub may itself run cells (the curator, later)
  or be a name only — the tier exists the moment something is published
  into it.
* **Approver tier is not yet checked.** HG-40.1 wants hub promotions
  approved at operator tier; an oversight decision carries `approver_id`
  but not a tier today. Phase 2 (needs the decision payload to carry the
  principal's tier, which the TUI now knows).

## Not here

Per-requester views on the Board / Compliance / Comms (HG-40.1 item 4);
cross-instance erasure by person across *all* instances (item 9 — `forget`
runs per collective and now reaches its hub; a sweep across every instance
is a loop over them); retention per tier (item 10); the curator role.yaml and
its schedule (item 11's runtime half); an approver-tier check; the curator
proposing *cross-instance questions* (routing — the A2A bridge is the
substrate, untouched here).
