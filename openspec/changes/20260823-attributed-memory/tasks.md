# Tasks: 20260823-attributed-memory

## Phase 0 — Decisions

- [ ] `[1]` Settle **floor vs delegation** for action authority (carried from OC-04).
      Recommendation: floor — 2604.08567 measures models failing to hold a
      delegation boundary across turns, and the floor rule does not ask them to
- [ ] `[2]` Settle the **information rule**: does a memory fragment carry the
      authority level of the context that produced it, and may it be refused
      retrieval into a lower context? Neither OC-04 candidate covers this,
      because both govern actions and this leak is in retrieval
- [ ] `[3]` Settle the **group-channel default**: one pooled context, or N private
      ones? This is the row of the scope table with real consequences
- [ ] `[4]` Settle **quorum size *k***, and whether an operator may override to
      promote a single-source note

> `[1]`–`[4]` gate Phase 3 onward. **Phases 1–2 are unblocked by all of them** —
> attribution and scoping are correct under either authority rule, and shipping
> them early is the whole sequencing argument.

## Phase 1 — Attribute

- [ ] `[5]` `requester` column (utf8, indexed) on the `episodes` schema in
      `acc/backends/vector_lancedb.py`
- [ ] `[6]` Populate it from the task payload's `requested_by` at
      `acc/cognitive_core.py:2317`
- [ ] `[7]` `owner` field on `SessionInfo` (`acc/sessions.py:58`), populated from
      the admitting principal
- [ ] `[8]` Replace `source_count` on `memory_notes` with `source_ids` and
      `source_requesters`; keep `source_count` derivable so nothing that reads it
      today breaks
- [ ] `[9]` Migration: pre-existing rows read as **unattributed** — never as
      belonging to the current requester. A migration that silently assigns
      ownership is worse than one that admits it cannot
- [ ] `[10]` Unattributed rows are excluded from every scoped result

## Phase 2 — Scope at retrieval

- [ ] `[11]` Requester filter in `_retrieve_episodes`, beside the existing
      `agent_id` filter (`acc/cognitive_core.py:2264`) — **not** in the prompt.
      A boundary the model is asked to respect is not a boundary
- [ ] `[12]` Scope default resolved from the **surface**, not a global setting:
      pooled for a single operator and a symmetric shared console, per-requester
      for a mixed-authority collective, per-group for a channel, **isolated and
      never pooled** for compat / webhook / subscription
- [ ] `[13]` Single-operator path is a no-op — no new required configuration and
      byte-identical retrieval. If this feature adds friction at the edge it gets
      disabled, in exactly the deployments that later grow a second user

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

- [ ] `[28]` Test: an admitted task produces an episode whose `requester` matches
      `admission.principal.attribution()`
- [ ] `[29]` Test: a pre-migration row reads unattributed and never appears in a
      scoped result
- [ ] `[30]` Test: two requesters on one agent cannot retrieve each other's
      episodes under per-requester scope
- [ ] `[31]` Test: single-operator retrieval is unchanged — the regression that
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
