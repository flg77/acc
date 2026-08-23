# Tasks: 20260823-attributed-memory

## Phase 0 — Decisions (all settled 2026-08-23)

- [x] `[1]` **Floor vs delegation** → **FLOOR**, and delegation is not offered as
      a mode. Its only benefit — a low-authority requester getting a high-category
      action done — is already provided by the oversight queue, *with an approver*.
      Delegation is not a missing capability; it is a way to skip an approval that
      already exists
- [x] `[1b]` **Consequence to record, not build here:** tiers and categories are
      different axes, so the floor rule needs a **per-principal category ceiling**
      (`effective = role grants ∩ principal ceiling`), which ACC cannot currently
      express. Separate change; noted so it is not rediscovered later
- [x] `[2]` **Information rule** → **STANDS.** A fragment carries the effective
      category ceiling of the task that produced it and is not retrieved below it.
      It uses authority as an imperfect proxy for sensitivity — chosen because the
      alternative (content classification) is the model-centric approach measured
      at 15.8–50.9%. It will over-restrict, which is the recoverable direction to
      fail in, and that is what makes Phase 4 load-bearing rather than optional
- [x] `[3]` **Group-channel default** → **POOLED, keyed on the channel, never on
      the participant set.** Per-person memory would make the agent worse at what a
      channel is for. Keying on participants would drag their DMs in. Invariant:
      *a channel's memory approximates its scrollback*. DMs are their own scope
- [x] `[4]` **Quorum** → **k = 2**, not 3. The epistemic jump is 1 → 2; a fixed k
      does not survive team size (k = 3 promotes nothing on a team of four and is
      trivial in a channel of fifty). The **approver** is the real check and must
      see the sources, since two people in one channel are not independent.
      Operator override to a single source is permitted, and the note is **marked
      single-source** — the marking is the point, not the permission
- [x] `[4b]` **Erasure vs audit immutability** → **different objects.** The audit
      records *that* something happened; memory records *what was said*. Constraint
      this imposes: episode `payload_json` is content, so the audit trail must not
      depend on episode content for its integrity — break that coupling before
      Phase 6 if it exists

> None of the five changes Phase 1 or Phase 2. Attribution and scoping are correct
> under every one of them, which is why they were built first.

## Phase 1 — Attribute (done 2026-08-23)

- [x] `[5]` `requester` column (utf8, indexed) on the `episodes` schema in
      `acc/backends/vector_lancedb.py`
- [x] `[6]` Populate it from the task payload's `requested_by` at
      `acc/cognitive_core.py:2317`
- [x] `[7]` `owner` field on `SessionInfo` (`acc/sessions.py:58`), populated from
      the admitting principal
- [x] `[8]` Replace `source_count` on `memory_notes` with `source_ids` and
      `source_requesters`; keep `source_count` derivable so nothing that reads it
      today breaks
- [x] `[9]` Migration: pre-existing rows read as **unattributed** — never as
      belonging to the current requester. A migration that silently assigns
      ownership is worse than one that admits it cannot
- [x] `[10]` Unattributed rows are excluded from every scoped result —
      `attribution.distinct_requesters()` counts *people*, and the sentinel is
      never one of them
- [x] `[10b]` **Found while building:** `create_table(exist_ok=True)` RAISES on a
      schema mismatch, so the migration had to run *before* it, not after. As
      first written this change would have made every existing LanceDB fail to
      open. `create_table_if_absent` now pre-checks — which is what the Milvus
      backend already did
- [x] `[10c]` **Found while building:** `list_tables()` supersedes
      `table_names()` but returns a response *object*, not names. Swapping them
      blindly makes every lookup miss, which reads as "no tables yet" and sends
      the caller down the create path. `_table_names()` handles both

## Phase 2 — Scope at retrieval (done 2026-08-23)

- [x] `[11]` Scope filter in `_retrieve_episodes`, beside the existing `agent_id`
      filter — **not** in the prompt. A boundary the model is asked to respect is
      not a boundary
- [x] `[11b]` **The settled answer to question 3 changed this task's shape.**
      A requester filter cannot express "everyone in this channel": a channel is
      a context and the people in it are not. So episodes carry a `scope` column
      as well as a `requester`, and `acc/memory_scope.py` computes it. That
      column was not in the Phase 1 plan — the group answer created it
- [x] `[11c]` **The mode is applied at WRITE time, not read time.** Retrieval is
      one equality test with no policy logic in it, so a mode cannot be
      *almost* applied. It also means changing a surface's mode later does not
      silently re-partition history: old episodes keep their old key and stop
      being reachable rather than being re-sorted into groups nobody consented
      to. Fails closed
- [x] `[12]` Scope default resolved from the **surface**: pooled for tui/webgui,
      per-group for slack, per-requester for voice, **isolated** for compat /
      webhook / subscription — and **isolated for any surface not in the table**,
      because the next adapter added is the one most likely to be missing from it
      and the failure that matters is its callers quietly sharing a memory
- [x] `[12b]` A **direct message is not a room**. Under a per-group mode a DM
      falls back to per-requester, or every private conversation on the platform
      would land in one memory
- [x] `[13]` Single-operator path is a no-op: work that never passed admission
      is scoped `local`, and the migration backfills pre-existing rows to the
      same scope, so a lone operator's retrieval is unchanged and loses no
      history
- [x] `[13b]` Task `[10]` now has teeth: an **attributed** context never sees
      unattributed history. Pre-attribution rows stay reachable from the console
      and cannot surface inside a channel

- [x] `[13c]` **Recall, found while building.** Both filters run *after* the
      vector search, so a busy neighbouring scope can fill the top-k and leave
      the requester with nothing — which reads as "the agent forgot", not as a
      partition working. With one operator this barely mattered; scopes
      partition far harder. Retrieval now over-fetches and truncates, which
      turns the common case from empty into fewer

> **Follow-up this creates.** Over-fetching bounds the damage; it does not
> remove it. A deployment with many active scopes can still starve a quiet one.
> The real fix is a **prefilter in the backend query** (LanceDB `.where()`),
> which changes the `VectorBackend.search` contract and has to hold across
> LanceDB, TurboVec and Milvus — a separate change, not a line in this one.

> **Open, and deliberately not built here.** Scenario S2 — a mixed-authority
> collective — is not expressible by a table keyed on the *surface*, because a
> compliance officer and an ML engineer share one. Distinguishing them needs the
> per-principal category ceiling recorded in `[1b]`, not another scope mode.
> Building a config override for it now would be guessing at the shape of a
> decision that has not been made.

## Phase 3 — Two tiers

- [ ] `[14]` `tier` on `memory_notes`: `private` | `shared`. `memory_reflection`
      writes `private`
- [ ] `[15]` Prompt-build reads the requester's private notes plus the shared
      notes their context admits (`acc/cognitive_core.py:2002`)
- [ ] `[16]` Exclude unattributed episodes **and** episodes from unattended
      ingress from distillation entirely. Anything that can prompt the collective
      must not be able to write what every future prompt reads — this is the
      cheapest attack in the change and the hardest to notice

## Phase 4 — Promotion as a proposal

- [ ] `[17]` Fourth `assistant_proposal` kind: `publish`, carrying the note, its
      source contexts, its destination context and its quorum evidence
- [ ] `[18]` Classify `publish` **structural** (like `spawn` / `role_update`) and
      gate at **Cat-B**, so the floor holds even in AUTO mode
- [ ] `[19]` The Compliance queue renders **both** contexts — an approver who
      cannot see the destination cannot judge the flow
- [ ] `[20]` Approval recorded against a named principal, not "the operator"

## Phase 5 — Quorum, dissent, probation

- [ ] `[21]` Promotion requires *k* **distinct human sources** (default 3). Ten
      episodes from one person is one person's opinion
- [ ] `[22]` Contradicting episodes recorded as **dissent** on the note rather
      than smoothed away. A lesson two people found true and one found false is
      more useful with the disagreement attached
- [ ] `[23]` Probation before a new shared note enters the Redis hot cache — an
      early note is disproportionately influential (2603.24676)
- [ ] `[24]` Top-N notes injected per prompt becomes a **governed setting**.
      Bandwidth is a regime variable, not a context-budget constant

## Phase 6 — Erasure

- [ ] `[25]` `acc-cli memory forget --requester <id>` — delete that requester's
      episodes and invalidate or rebuild every note carrying them in `source_ids`
- [ ] `[26]` A note falling below quorum after removal is **demoted to private,
      not deleted**, and the demotion is journalled
- [ ] `[27]` Erasure touches the memory tier only; the audit record keeps the
      *fact* of a request without its content

## Phase 7 — Verification

- [x] `[28]` Test: an admitted task produces an episode whose `requester` matches
      `admission.principal.attribution()`
- [x] `[29]` Test: a pre-migration row reads unattributed and never appears in a
      scoped result
- [x] `[30]` Test: two requesters on one agent cannot retrieve each other's
      episodes under per-requester scope
- [x] `[31]` Test: single-operator retrieval is unchanged — the regression that
      matters most, on the most-used path
- [ ] `[32]` Test: a note distilled from A's episodes does not reach B's prompt
- [ ] `[33]` Test: a webhook-sourced episode never enters a note
- [ ] `[34]` Test: `publish` **never** auto-executes, in any operating mode —
      asserted on the absence, since the dangerous version of this feature is the
      one that publishes helpfully
- [ ] `[35]` Test: ten episodes from one requester do not satisfy `k = 3`
- [ ] `[36]` Test: a note with recorded dissent renders its disagreement
- [ ] `[37]` Test: after `forget --requester A`, no note retains an A-sourced id,
      and a note dropping below `k` is demoted and journalled
