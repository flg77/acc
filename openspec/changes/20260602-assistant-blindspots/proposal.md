# 20260602-assistant-blindspots — proposal

## Why

A live lighthouse trace on 2026-06-02 (AUTO mode, frozen policy banner)
showed the Assistant failing in five distinct ways inside one
conversation. The operator asked "but isn't there the deep-research
role?", the Assistant denied knowing about it, then emitted a
hallucinated `research_agent` marker wrapped in **backticks**:

```
`PROPOSE_SPAWN:role:research_agent:investigation`
```

Five gaps surfaced (each verified against the codebase):

1. **Marker-form drift.** `acc/assistant_proposal.py` only accepts
   square-bracket markers; the backtick variant is silently dropped,
   so v0.3.45's `validate_marker` never sees the hallucinated role and
   no warning lands in the log.
2. **Catalog truncation.** `acc/perception.py:_render_control` caps
   the "Available roles" list at 25 entries and emits a single
   `- ... and N more` line. With a 30+ role catalog, the LLM literally
   cannot see whether a role exists in the tail.
3. **No mid-task capability follow-up.** The Assistant has no way to
   issue a fresh `capability_query` mid-reasoning. The snapshot fires
   once before the LLM call and nothing after.
4. **No sub-collective discovery.** `managed_sub_collectives` is
   loaded only from the hub `collective.yaml`. The operator clearly
   has a sibling `acc-researcher` workspace, but there's no path for
   the Assistant to learn about it without checked-in YAML changes.
5. **No correction memory under AUTO.** The operator's corrections
   ("don't we need MCPs?", "isn't there a deep-research role?") leave
   no record. AUTO mode freezes policy by design (SIP rail 6), but
   the memory chain doesn't pick up the correction either, so the
   next task sees no trace.

## What changes

Three phases. Phase 1 ships immediately and matches the symptoms 1:1.
Phases 2 and 3 are designed but deferred.

### Phase 1 — cheap, high-leverage (this ship)

* **1.1 Marker-form tolerance** — `parse_proposal_markers` pre-
  normalises three delimiter forms (canonical, backtick-wrapped,
  bare-line) into the canonical shape so the strict per-marker
  regexes match. Downstream `validate_marker(profile, snapshot,
  marker, role=role)` then catches the hallucinated role name.
* **1.2 Kill the truncation cliff** — `_render_control` raises the
  detailed cap (env-tunable, default 40) and emits any overflow as
  a single comma-joined name-only tail line so the LLM at least
  sees the names. Block stays under 8 KB at 200-role catalog size.
* **1.3 Filesystem sub-collective discovery** — `load_collective`
  optionally scans `ACC_DISCOVER_SUBCOLLECTIVES_ROOT` (default
  unset; opt-in) for sibling `*/collective.yaml` files; each yields
  an entry on `managed_sub_collectives` with domain + description
  lifted from the sibling's `role_definition` overlay. Hub-declared
  entries win over discovered ones — purely additive.

### Phase 2 — mid-task capability question (deferred)

New marker `[ASK_CAPABILITY:kind:query]`. `cognitive_core` intercepts,
fires fresh `capability_query`, appends the reply as a user-turn
`## Capability follow-up`, gives the LLM one more pass. Capped at one
ASK round-trip per task. Reuses
`acc/signals.py:subject_capability_query` and the v0.3.42
CapabilityIndex.

### Phase 3 — correction memory under AUTO (deferred)

Operator follow-ups that pattern-match correction shape ("isn't there",
"shouldn't you", "but") record a `CORRECTION` episode with the operator
text + the assistant's prior reply. Independent of SIP — memory chain
picks it up via the next reflection pass; perception block reads the
resulting `memory_notes` on the next task.

## Impact

* **Affected code (Phase 1):**
  * `acc/assistant_proposal.py` — `_normalize_marker_delimiters` +
    `parse_proposal_markers` wrapping
  * `acc/perception.py` — `_DETAILED_ROLE_CAP` constant +
    `_render_control` overflow logic
  * `acc/collective.py` — `_merge_discovered_subcollectives` +
    `load_collective` wiring
* **New env vars:**
  * `ACC_DISCOVER_SUBCOLLECTIVES_ROOT` (default unset; opt-in)
  * `ACC_PERCEPTION_DETAILED_ROLE_CAP` (default 40)
* **Tests:** 28 new across three files.
* **Backward compatibility:** purely additive. Canonical marker form
  still parses byte-identically; default catalog cap raised from 25
  to 40 (a render improvement, not a behaviour change for callers);
  filesystem discovery is opt-in.

## What stays open after Phase 1

* No mid-task `[ASK_CAPABILITY]` round-trip (Phase 2).
* No correction memory under frozen policy (Phase 3).
* No NATS / A2A cross-collective discovery — filesystem only.
* No automated detection of marker names outside the parser's enum
  (e.g. the LLM emitting `[QUERY_ORCHESTRATOR:...]`) — silent drop
  today, becomes Phase 2's territory.
