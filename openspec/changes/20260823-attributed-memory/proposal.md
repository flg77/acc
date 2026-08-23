# Proposal: Attributed memory — requester identity past the admission boundary

**Change ID:** 20260823-attributed-memory
**Date:** 2026-08-23
**Status:** Draft (three decisions open; P1 and P2 are unblocked)
**Author:** flg

---

## Problem Statement

v0.8.0 shipped requester identity at the admission boundary. `acc/identity.py`
resolves a `Principal` from the substrate — cluster RBAC, edge system auth, the
web session — and `acc/channel_access.py` is the single point where an inbound
message becomes a task, journalling every decision and stamping
`task_attribution()` onto the payload.

**That identity survives exactly one hop.** It is not a column on `episodes`,
not a field on `SessionInfo`, not a filter in `_retrieve_episodes`, and not a
property of a `memory_note`. ACC knows who asked at the door and has forgotten
by the time anything is written down.

This was invisible while ACC was single-operator. It is not a future problem:
episode retrieval scopes on `agent_id` alone
(`acc/cognitive_core.py:2264`), so two people in one Slack channel — or two
callers of the compat endpoint — reach the same agent, share one episode pool,
and can each retrieve the other's history. That became true the moment a second
prompt surface existed. Nobody decided it.

## Current Behavior

| Layer | Carries the requester? | Where |
|---|---|---|
| Admission decision | **yes** | `acc/channel_access.py:96` |
| Task payload | **yes** | `acc/channel_access.py:80` — `task_attribution()` |
| Session record | no | `acc/sessions.py:58` — `SessionInfo` has no owner |
| Episode row | opaque | `acc/backends/vector_lancedb.py:16` — inside `payload_json` only |
| Episode retrieval | no | `acc/cognitive_core.py:2264` — `agent_id` filter only |
| Memory note | no | `acc/backends/vector_lancedb.py:85` — `source_count` is an `int64` |
| Prompt injection | no | `acc/cognitive_core.py:2002` — one note block per role |

The `episodes` schema is `id, agent_id, ts, signal_type, payload_json,
embedding`. The requester is inside the JSON blob when an adapter stamped it,
but it is not a column: not filterable at query time, not indexable, not
redactable without rewriting blobs.

`memory_reflection` clusters recent episodes, has the LLM distil one durable
lesson per cluster, writes it to `memory_notes`, and pushes the top-N summaries
into a Redis per-role hot cache read on the prompt-build path. The note records
`source_count` — *how many* episodes, never *which*.

## The thing worth noticing

ACC already aggregates individual learnings and feeds them back to a shared
surface. It does it per role, from whatever episodes an agent accumulated,
without consent, provenance or quorum, and without anyone having decided to.

So the collective-learning feature and the leak described above **are the same
pipe**. The only difference between them is governance. That reframes the work:
this is not an enhancement competing with the rest of the roadmap, it is the
governance of a flow that already exists.

`memory_reflection` defaults **off** per role (`acc/agent.py:3476`), so today
the exposure is latent. It goes live the first time reflection is enabled for a
role reachable from a shared channel — precisely the configuration an operator
chooses when they want this behaviour.

## Three defects, stated separately

**1. Cross-requester retrieval.** The `agent_id` filter is right for the world
it was written in — its comment says same-agent provenance keeps "do you
remember?" answers honest, and that is true. With two humans on one surface it
is the wrong *axis*: same agent, different person, no filter.

**2. Ownerless sessions.** Anyone at operator tier may `resume` any session. In
the target deployment — a compliance officer, an ML engineer and an auditor
sharing one ACC — that is an unaudited read of someone else's investigation.

**3. Provenance destroyed at distillation.** `source_count` is an integer, so
nobody can answer "which of my conversations produced this note?" and nobody can
remove a person's contribution afterwards. GDPR Art. 17 erasure is not
implementable against this schema — not difficult, **impossible**.

## Why enforcement belongs in retrieval

The tempting shortcut is to tell the agent who is asking and what each person
may see. The measurements say that fails:

- Frontier models "fail to maintain stable prioritization under conflicting user
  objectives" and show **increasing privacy violations over multi-turn
  interactions** (arXiv 2604.08567).
- Contextual-integrity violation rates in enterprise settings run **15.8–50.9%**,
  and *higher task utility correlates with more violations* — so it cannot be
  tuned away by making the agent better. The paper's conclusion is a shift
  "beyond model-centric scaling toward **context-centric architectures**"
  (arXiv 2604.21308).
- In social settings, leakage is **contagious**: agents are **8× more likely** to
  disclose after observing a peer do so, and explicit privacy instructions still
  leave **>37.8%** (arXiv 2605.27766).

A boundary the model is asked to respect is not a boundary. The filter goes next
to the `agent_id` filter, before the prompt is built.

## Why aggregation needs a quorum

A memory-note pool read on every prompt-build is a mutual in-context learning
channel, and those have a known failure mode: one agent's arbitrary sample
becomes the next one's evidence and compounds, producing a drift-dominated
regime where consensus is **"effectively a lottery"** (arXiv 2603.24676). The
same work makes prompt **bandwidth** — top-N notes injected — a regime variable
rather than a context-budget constant, and implies an early note is
disproportionately influential.

Separately, decentralised LLM populations develop **collective bias even when no
individual agent is biased** (arXiv 2410.08948). You do not need an adversary to
get a bad pool.

So promotion requires a quorum of **distinct human sources**, dissent is recorded
rather than smoothed, and a new shared note serves probation before it reaches
the hot cache. `source_count` cannot distinguish ten episodes from one person
from one episode each from ten — which is another way of saying defect 3 blocks
this outright.

## Proposed Behavior

Six steps. The first two are corrections, not features, and ship alone.

**1. Attribute.** `requester` column on `episodes` (indexed); `owner` on
`SessionInfo`; `source_ids` + `source_requesters` replacing the bare
`source_count` on `memory_notes`. Pre-migration rows read as **unattributed** and
are excluded from scoped results — a migration that silently assigns ownership is
worse than one that admits it cannot.

**2. Scope.** A requester filter beside the `agent_id` filter, with the default
being a **property of the surface**:

| Surface | Default scope |
|---|---|
| TUI / WebGUI, single operator | pooled (filter is a no-op) |
| Shared console, symmetric authority | pooled |
| Mixed-authority collective | **per requester** |
| Group channel | per group |
| Compat endpoint, webhook, subscription | **isolated; never pooled** |

**3. Two tiers.** Notes are `private` or `shared`; reflection writes `private`.
Unattributed episodes and episodes from unattended ingress are excluded from
distillation entirely — anything that can prompt the collective must not be able
to write what every future prompt reads.

**4. Promotion is a proposal.** A fourth `assistant_proposal` kind, `publish`,
carrying the note, its source contexts, its destination context and its quorum
evidence. Classified **structural** (like `spawn` and `role_update`) and gated at
**Cat-B**, so the floor holds even in AUTO mode.

**5. Quorum, dissent, probation.** *k* distinct human sources (default 3);
contradicting episodes recorded as dissent on the note; probation before the hot
cache; top-N as a governed setting.

**6. Erasure that reaches the aggregate.** `memory forget --requester` deletes
episodes and invalidates or rebuilds every note carrying them. A note that falls
below quorum is **demoted to private, not deleted**, so the demotion is visible.

## Scope

**In scope.** The memory half of the multi-human problem: attribution past
admission, retrieval scoping, the private/shared split, governed promotion,
erasure.

**Not in scope.**

- **A new identity model.** Principals keep coming from the substrate. No
  directory, no user store, no fourth authentication path.
- **Cross-deployment note federation.** A note crossing a policy domain needs the
  same treatment `20260820-sandbox-gateway-failover` gave a cage crossing one:
  verified equivalence, not an assertion. Deferred to sequence with A2A.
- **Prompt-level enforcement**, for the reasons measured above.
- **Transcript sharing.** What crosses a context is a distilled lesson — never a
  transcript, never a quote attributable to someone who did not consent to being
  quoted outside their context.
- **Audit-trail mutability.** Erasure operates on the memory tier. The audit
  record stays append-only, retaining the *fact* of a request without its
  content.
- **Automatic publication**, in any operating mode.

## The rule this change adds

`OC-04` left one question open: does requester authority compose with role
authority by the **floor** rule (effective = min(role, requester)) or the
**delegation** rule (the agent acts as the requester, role grants are a ceiling)?

That question is still open, but **neither candidate answers what memory does**,
because both are rules about *actions*:

> A note distilled from a Cat-C operator session and injected into a Cat-B
> requester's prompt moves privileged content across an authority boundary
> **without any action crossing it.** The floor rule does not fire — nothing was
> attempted. Neither does delegation. The leak is in retrieval, which happens
> before the model has produced anything for a policy to evaluate, and
> prompt-build is upstream of every gate ACC has.

So this change proposes a third rule, stated as its own decision:

> **A memory fragment carries the authority level of the context that produced
> it, and may not be retrieved into a context below that level.**

Authority becomes a property of information, not only of action.

## Propose or alert

`20260820-sandbox-gateway-failover` settled that a sustained gateway outage
raises an **alert**, never a proposal, because every action a proposal could
offer there reduces a control.

Publication is the other side of that rule and it is worth naming why they
differ. A distilled note is bounded and legible — a human reads the text and
decides whether it may cross. "Approve running without the cage" cannot be read.

> **Propose when the blast radius is inspectable. Alert when it is not.**

## Sequencing

Order is forced by one constraint: **provenance cannot be retrofitted onto a pool
that already exists.**

Steps 1–2 are prerequisites for channel breadth and for webhook ingress. Both
multiply the number of unattributed writers into a shared pool. An access-control
gap becomes visible the day it is exploited; a contaminated memory pool does not
become visible at all.

## Open questions

1. **Floor rule or delegation rule** for *action* authority — carried over from
   `OC-04`, still unanswered. Recommendation: **floor**, since 2604.08567 shows
   models do not hold a delegation boundary across turns and the floor rule does
   not ask them to.
2. **Does the information rule above stand** — a fragment carries its originating
   context's authority and may not be retrieved below it?
3. **Default scope for a group channel** — one pooled context or N private ones?
   The consequential row of the scope table.
4. **Quorum size *k***, and whether a single-source note may ever be promoted by
   operator override.
5. **Erasure versus audit immutability.** These pull against each other; the
   tension is real, not a wording problem.

Questions 1–2 gate step 3 onward. **Steps 1 and 2 are unblocked by all five** —
attribution and scoping are correct under either authority rule.

## Related

- `openspec/changes/20260817-session-resume-and-lifecycle` — `SessionInfo` gains
  the `owner` this change needs
- `openspec/changes/20260820-sandbox-gateway-failover` — the verified-equivalence
  argument reused for cross-domain notes, and the propose/alert rule
- ACC Roadmap **OC-04** (multi-human surfaces), **OC-05** (channel breadth) and
  **OC-07** (webhook ingress) — the last two sequence behind steps 1–2
- Threat model **ACC-TM-06** (unauthenticated requester) — currently in flight on
  `feat/evidence-integrity`, which records that ACC-TM-08 and ACC-TM-13 cannot be
  closed while it is open. This change closes the memory half of it.
- Vault: `ACC Roadmap/OC-04 in depth — multi-human surfaces and the
  collective-learning question` (analysis, eight deployment scenarios, the arXiv
  evidence) and `ACC Roadmap/Proposals/RP-01 — Attributed memory and governed
  collective learning` (the proposal this change implements)
