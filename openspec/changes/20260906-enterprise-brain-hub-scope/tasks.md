# 20260906-enterprise-brain-hub-scope — tasks

## Phase 1 — the hub tier + the information rule (2026-09-07)
- [x] `memory_reflection`: `HUB_TIER`, `hub_destination()`, `parse_destination()`,
      `note_ceiling(episodes)` (highest source ceiling; unattributed = CRITICAL),
      `MemoryNote.ceiling` stamped by `consolidate`
- [x] hot-cache entries are dicts: summary, note_id, source_requesters, ceiling, scope,
      dissent (`_raw_note_entries` already normalised str|dict); `list_notes()`
- [x] `read_hot_cache(reader_ceiling=, hub_collective_id=)`: third read of the hub's
      enterprise tier when bound (never a peer, never itself twice); entries above the
      reader's ceiling skipped; bare entries read as CRITICAL
- [x] `publish_note(ceiling=, source_requesters=, note_id=)` carries them on the copy
- [x] `assistant_proposal`: `build_publish_proposal` carries `ceiling`; `_dispatch_publish`
      resolves `hub:<cid>` to the hub's collective + `enterprise`, journals
      `destination_collective` + `ceiling`
- [x] `cognitive_core`: `hub_collective_id` ctor arg (agent passes the config value);
      `_read_memory_notes(reader_ceiling=ceiling_of(task_payload))`
- [x] `memory_forget.forget_person(hub_collective_id=)` → `_unpublish` reaches the hub;
      report names `hub:<cid>`
- [x] `acc/memory_curate.py`: `candidates(redis, hub, k=, collectives=, now=)` +
      `Candidate.note()`
- [x] `acc-cli memory notes --hub`, `memory propose`, `memory curate [--propose]`;
      `memory forget` passes the hub
- [x] tests `tests/test_hub_scope.py` (16): destinations; proposal carries ceiling + hub;
      quorum still guards; approved hub publication lands under the hub and nowhere
      else; plain destinations unchanged; bound collective reads own + hub, never a
      peer; unbound never reads the hub; hub never twice; note ceiling = highest source
      / unattributed = CRITICAL; consolidate stamps it; cache entries carry id / people /
      ceiling; reader below the ceiling never sees the note (own, shared, hub); pre-ceiling
      cache reads as CRITICAL; forget unpublishes from the hub; curate finds qualifying
      notes, skips single-source / already-in-hub / hub-own, respects probation, limits to
      bound collectives
- [x] docs: CHANGELOG, CAPABILITIES row, MANUAL memory line

## Phase 2 — the curator and the checks (2026-09-07)
- [x] `roles/hub_curator/role.yaml`: no chat surface (`chat_surface: false` — the task loop
      drops `TASK_ASSIGN`), no skills / MCPs / actions, `curate_interval_s: 900`; known to the
      operator's `known_roles.txt`; deliberately not in `CONTROL_ROLES` (those resolve only from
      the signed control-roles pack, whose fixture needs the signing key to rebuild)
- [x] `Agent._curate_once` / `_curator_loop`: `memory_curate.candidates(redis, hub=self)` →
      one publish proposal per candidate into the hub's own queue via the extracted
      `_queue_assistant_proposal` (row + cached payload + pending announcement); a note
      proposed once is remembered for 7 days (`acc:<hub>:curator:proposed`)
- [x] the decision carries `approver_tier` (TUI from the resolved principal, Web GUI from the
      session role, CLI from `identity.current()`); `dispatch_approved_proposal(approver_tier=)`;
      `_dispatch_publish` refuses a hub destination unless operator tier — fail closed,
      journalled as `note_publish_refused`
- [ ] two-approver promotions (HG-40.1 §2.5 "2 distinct people approve"): needs a
      multi-decision proposal state; D-013 made a decision final — a deliberate later change
- [ ] lighthouse: two instances bound to one hub; a note published from one reaches the
      other on the prompt path and nothing else does; a MEDIUM requester never sees a
      CRITICAL hub note; `memory forget` in one instance pulls the note from the hub
- [ ] per-requester views on the Board / Compliance / Comms (HG-40.1 item 4)

## Phase 3 — the long run
- [ ] retention per tier (episodes age out, private notes TTL unless promoted, hub notes
      carry a review date) — one reaper
- [ ] erasure by person across every instance on the host (a loop over `forget` per
      collective + the hub)

## Depends on / feeds
- `20260906-acc-instance` (`hub` on the instance → `ACC_HUB_COLLECTIVE_ID` → the core's
  hub read)
- D-014 ceilings (`ceiling_of` on the reader side, `requester_ceiling` on the sources)
- `20260823-attributed-memory` [2] — now enforced (its tasks.md notes say "not enforced")
