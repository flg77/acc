# 20260604-role-ecosystem-strategy — tasks

## Phase 1 (this ship) — strategy ratification

### 1.1 Operator-facing synthesis
- [ ] Create `docs/architecture/role-ecosystem.md` — render the
      synthesis from `proposal.md` in the operator's voice (not the
      OpenSpec contributor's). Suitable for posting on
      acc-web-project / Developer page.

### 1.2 Formal strategy spec
- [ ] Create `openspec/specs/role-ecosystem/strategy.md` — the
      Phase A-F sequencing reference + revenue-stream pricing
      bands + slim-core image-size targets as SHALL statements.

### 1.3 Cross-references in existing proposals
- [ ] Add reference block to
      `openspec/changes/20260531-acc-role-package-format/proposal.md`
      pointing at this strategy as the "why + when" anchor.
- [ ] Add Phase 6 placeholder to
      `openspec/changes/20260603-capability-pool/tasks.md`:
      "Extract from core into `@acc/baseline` package" gated on
      Phase B.
- [ ] Add reference block to
      `openspec/changes/20260604-role-proposal-finance-agentset/proposal.md`
      noting candidacy as first verified-publisher subject.

### 1.4 Operator-facing brainstorm copy
- [ ] (Done) Brainstorm at
      `<Obsidian>/ACC-Role-Ecosystem/Role Ecosystem — brainstorm.md`.
- [ ] (Done) Index at
      `<Obsidian>/ACC-Role-Ecosystem/ACC-Role-Ecosystem — index.md`.

### Verification (Phase 1)
- [ ] `pytest tests/ --ignore=tests/container --no-cov -q` — sanity
      check that the doc additions don't break anything (target
      2577+ passing).
- [ ] Manual review: a reader new to ACC can read
      `docs/architecture/role-ecosystem.md` and understand both
      the technical sequencing AND the revenue model.

## Phase A (deferred — see 20260531-acc-role-package-format Phase 1)
Role-package format v1: builder / verifier / installer CLI, .accpkg
manifest schema v1, coding_agent migration pilot, reference golden
package, formal manifest spec.

## Phase B (deferred — see 20260531-acc-role-package-format Phase 3)
Slim-core: extract `@acc/workspace-roles`, `@acc/research-roles`,
`@acc/business-roles` from in-tree `roles/` into externally-
versioned packages. Edge image → ~80 MB. Standalone image → ~250 MB.

## Phase C (deferred — see 20260531-acc-role-package-format Phase 4)
Hub MVP: static read-only hub on GitHub Pages / S3,
`acc-pkg install @scope/name@version` CLI.

## Phase D (deferred — see 20260531-acc-role-package-format Phase 5)
Marketplace UX: extend `acc-web-project /roles` page from read-only
to publish + install; TUI Marketplace pane (gatekeeper → AoA-P2b
queue → install).

## Phase E (deferred — NEW; needs its own follow-up proposals)
Commercial layer:
- [ ] Verified-Publisher admin UI + subscription billing
- [ ] Marketplace transaction layer (Stripe Connect)
- [ ] Premium pack billing (per-seat)
- [ ] Hosted runtime MVP (`acc.cloud`)
- [ ] Split into `acc-cloud` repository (per Question 5)

These each need their own OpenSpec proposals when the operator
engages commercial scope.

## Phase F (deferred — see 20260531-acc-role-package-format Phase 7)
Federation: A2A AgentCard cross-hub discovery, private corporate
hubs.

## Strategic decisions (block Phase E start)

- [ ] Q1: OSS license discipline — Apache 2.0 + CLA (recommended)?
- [ ] Q2: Hub hosting authority — single canonical hub (recommended)?
- [ ] Q3: Verified-Publisher pricing — free for OSS (recommended)?
- [ ] Q4: Phase B trigger — v0.4.0 (recommended) or sooner?
- [ ] Q5: Phase E ownership — separate `acc-cloud` repo (recommended)?
