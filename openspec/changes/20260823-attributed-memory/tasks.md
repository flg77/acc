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

## Phase 3 — Two tiers (done 2026-08-23)

- [x] `[14]` `tier` on `memory_notes`: `private` | `shared`. `memory_reflection`
      writes `private`, and there is a test asserting it cannot write the shared
      key even when handed a note already marked shared — the dangerous version
      of this feature is the one that promotes helpfully
- [x] `[14b]` **The defect this phase actually had to fix.** `consolidate()`
      clustered the *whole* recent ring and summarised across it, so one note
      could be distilled from two channels at once. Phase 2's retrieval filter
      would never have caught it: notes bypass episode retrieval entirely, so
      the leak arrives **already summarised**, in every prompt, attributed to
      nobody. Clustering now happens strictly within a scope
- [x] `[14c]` The Redis hot cache is **per scope**, not per role. One key held
      notes from every context and was read on every prompt-build. Old keys are
      not deleted — nothing reads them, and the existing TTL retires them
- [x] `[15]` Prompt-build reads the asking task's private notes plus the shared
      tier. Nothing writes shared yet (Phase 4), so the second read is always a
      miss — wired now so promotion is a change of *state*, not of shape
- [x] `[16]` Episodes from **isolated** surfaces — compat, webhook,
      subscription, and any surface not in the mode table — are dropped before
      clustering, not filtered after
- [x] `[16b]` **Task `[16]` as written would have broken the main use case.**
      "Exclude unattributed episodes" reads well until you notice the TUI never
      passes through admission, so *every* single-operator episode is
      unattributed: the blanket rule switches reflection off for the deployment
      it was built for. Caught by an existing test, not by review. The
      discriminator is the **scope's mode**, not attribution; unattributed
      material is instead held back at the *promotion* boundary, where the
      Phase 5 quorum counts distinct people and finds none. Local notes stay
      private to local forever, which is the intended outcome by a route that
      does not regress anything

> **Open, and it must be closed before Phase 4 ships.** The shared read is wired
> but the authority check that has to gate it — settled question 2, *a fragment
> carries the ceiling of the context that produced it and is not retrieved below
> it* — is **not enforced**, because per-principal ceilings do not exist yet
> (task `[1b]`). Nothing can reach the shared tier until promotion exists, so
> the gap is not reachable today. It becomes reachable the moment Phase 4 lands.

## Phase 4 — Promotion as a proposal (done 2026-08-23)

- [x] `[17]` Fourth kind `PROPOSAL_PUBLISH`, carrying the note, its source
      context, its destination context and its quorum evidence.
      `build_publish_proposal()` assembles it; `_dispatch_publish()` applies it
- [x] `[17b]` **Publication is DIRECTED, and that dissolved the Phase 3
      blocker.** Phase 3 modelled the shared tier as one broadcast blob, which
      is exactly what made settled question 2 unenforceable: a broadcast has no
      destination for a human to approve, so nothing could check where a note
      ended up. Keyed on the destination instead, **a note is readable exactly
      where a person put it** — and "may this fragment be retrieved in that
      context?" is answered by the approval record rather than by a ceiling
      comparison that does not exist yet
- [x] `[18]` `publish` is HIGH risk and in `_NEVER_AUTOEXEC`, with **no escape
      hatch** — unlike `INFUSE`'s dev-mode one, which was deliberately left
      INFUSE-only. An auto-executing publication is not a faster version of the
      decision; it is the absence of it
- [x] `[19]` The proposal summary names **both** contexts and the count of
      distinct requesters, and marks a single-source note as such. An approver
      who cannot see where a note came from is clicking on prose
- [x] `[20]` The approver reaches the proposal. `approver_id` already existed on
      the oversight decision and was **dropped** between the queue and the
      dispatcher; it is now stamped onto `operator_id` and carried on the
      journal entry
- [x] `[20b]` **A publication nobody can be named for is refused**, not warned.
      `"tui:anonymous"` is the fallback when a surface sends no approver, and
      accepting it would record an approval nobody can be held to — the same as
      no approval. Refused rather than permitted because publication is new, so
      nothing depends on the permissive behaviour, and a control that fails open
      on its first day never gets tightened

> **What ceilings would still add.** The approver is currently the *only* check
> on whether a fragment may cross into a lower-authority context. Per-principal
> ceilings (task `[1b]`) would put a hard floor **under** that judgement, so a
> human could not approve a publication the policy forbids. That is a
> strengthening, no longer a prerequisite.

## Phase 5 — Quorum, dissent, probation (done 2026-08-24)

- [x] `[21]` Promotion requires *k* distinct **people**, default **2** per the
      settled answer (the task said 3). `build_publish_proposal` raises
      `QuorumNotMet` below the floor; an operator may override, and the note is
      then marked both single-source and overridden-by-whom
- [x] `[21b]` **Counts PEOPLE, not requester strings.** `Principal.attribution()`
      renders as `source:subject@scope`, so the same human in two rooms produces
      two requesters — and a quorum of two would be satisfied by one person
      talking to themselves next door. `person_of()` strips the room.
      Clustering is confined to one scope (Phase 3), so the scope suffix is
      constant within a note and this does not currently bite; it is stripped
      anyway, because otherwise the count is correct by coincidence and the
      coincidence ends the first time anything aggregates across scopes
- [x] `[21c]` This is also what holds back the operator's own `local` notes
      without switching reflection off: they have **zero** attributed people, so
      they can never be promoted, which is exactly the outcome task `[16]`
      wanted by a route that regresses nothing
- [x] `[22]` The summariser is asked, in the same call, for a `DISSENT:` line
      when an episode contradicts the lesson; it is recorded on the note and
      rendered as *"lesson (disputed: …)"*. Advisory, not a gate — a model can
      invent disagreement, and recording an invented caveat is a smaller error
      than silently averaging away a real one
- [x] `[23]` Probation before a published note is read on the prompt path
- [x] `[23b]` **Honest about what probation is: a revocation window, not a drift
      mitigation.** It gives a human time to see the publication in the journal
      and undo it. The drift 2603.24676 describes is addressed by `[21]` and
      `[24]` — a delay does not touch it, and saying otherwise would borrow
      credibility from a result that says something else
- [x] `[24]` `memory_note_bandwidth` is a **role field**, so widening it goes
      through `ROLE_UPDATE` and is countersigned like any other role change.
      Default 3, matching the constant it replaced, so nothing changes on
      upgrade. A zero is floored to one rather than silently muting memory

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
- [x] `[32]` Test: a note distilled from A's episodes does not reach B's prompt
- [x] `[33]` Test: a webhook-sourced episode never enters a note
- [x] `[34]` Test: `publish` **never** auto-executes, in any operating mode —
      asserted on the absence, since the dangerous version of this feature is the
      one that publishes helpfully
- [x] `[35]` Test: ten episodes from one requester do not satisfy `k = 3`
- [x] `[36]` Test: a note with recorded dissent renders its disagreement
- [ ] `[37]` Test: after `forget --requester A`, no note retains an A-sourced id,
      and a note dropping below `k` is demoted and journalled
