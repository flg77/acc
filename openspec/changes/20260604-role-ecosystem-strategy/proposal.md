# 20260604-role-ecosystem-strategy — proposal

## Why

Seven existing OpenSpec proposals touch the role-ecosystem question
piecewise; **no single document ties them together** or covers the
business model that makes the marketplace sustainable. As a result:

1. Engineering is **adding** roles to core (`20260603-capability-pool`
   Phase 1 flipped 50 role.yaml files in-tree; the image grew, not
   shrank), while the package-format proposal calls for slimming.
   The two streams are unaligned.
2. The slim-core target ("Phase 3" in
   `20260531-acc-role-package-format`) is named but unmeasured. No
   image-size number, no concrete extraction list.
3. The capitalization story is missing entirely. Without a revenue
   model, the marketplace work is engineering effort without payback
   accounting.
4. The verified-publisher tier is mentioned without pricing,
   eligibility, or process. Red Hat is named as a candidate;
   nothing else is specified.
5. License + IP discipline for premium / vertical packs isn't
   specified.

This proposal **synthesizes** the seven existing proposals,
**measures** the slim-core target, and **adds** the missing
business / capitalization layer. It does not invent new technical
mechanisms — every component already exists across the existing
proposals. The contribution is the **strategy + sequencing + revenue
model**.

## Existing proposals this synthesizes

| Proposal | Role in the ecosystem |
|---|---|
| `20260531-acc-role-package-format` | `.accpkg` format, hub, maturity ladder, trust model. **Core technical proposal**. |
| `20260603-capability-pool` | Skill + MCP catalog that travels INSIDE a package. |
| `20260531-role-perception-profiles` | Per-role substrate a package declares (`perception_profile`). |
| `20260530-role-proposal-assistant-agent-of-agents` | The gatekeeper that *consumes* packaged roles via the AoA-P2b queue (Phase 5 ⇒ Marketplace pane). |
| `20260527-agentcard-discovery` | Every installed package auto-publishes an AgentCard. |
| `20260527-a2a-agent-interop` | Cross-hub federation transport. |
| `20260530-acc-self-improvement-policy-gradient` | Community-vetted SIP policy bounds travel in `policy/policy-bounds.yaml`. |

Adjacent (touch indirectly):

* `20260604-role-proposal-finance-agentset` — first real example of
  a complete packaged agentset; likely first verified-publisher
  candidate.
* `20260530-role-proposal-dreamer-agent` — `memory-seed/` block in a
  package becomes the dreamer's starting corpus.
* `20260531-role-proposal-orchestrator-skills-mcp-specialist` —
  CapabilityIndex enumerates packaged skills/MCPs at infusion time.

## What changes

This proposal is a **strategy document**. The deliverable is the
synthesis below + a Phase ordering that the existing proposals
update to reference. No new code in Phase 1.

### Phase 1 (this ship) — strategy ratification

* **`openspec/changes/20260531-acc-role-package-format/proposal.md`**
  gains a *cross-reference* to this synthesis (no content change).
* **`openspec/changes/20260603-capability-pool/proposal.md`** gains
  a Phase 6 placeholder ("Extract from core into
  `@acc/baseline` package") gated on this synthesis's Phase B.
* **`docs/architecture/role-ecosystem.md`** — NEW: the
  operator-facing version of the synthesis below (the brainstorm
  rendered for the project website).
* **`openspec/specs/role-ecosystem/strategy.md`** — NEW: the
  formal strategy + sequencing reference.
* **No runtime code change.** Pure docs / cross-references.

### Phases A–F (deferred — they are the existing proposals' phases)

This proposal does NOT define new phases of engineering work. It
**sequences** the existing proposals' phases into a coherent
rollout:

```
Phase A — Role-package format v1  (= 20260531-acc-role-package-format Phase 1)
Phase B — Slim-core              (= 20260531-acc-role-package-format Phase 3)
Phase C — Hub MVP                (= 20260531-acc-role-package-format Phase 4)
Phase D — Marketplace UX         (= 20260531-acc-role-package-format Phase 5)
Phase E — Commercial layer       (NEW — Stream 2/3/4/5 of this proposal)
Phase F — Federation             (= 20260531-acc-role-package-format Phase 7)
```

**Phase E** is the only genuinely new phase in this synthesis. It
adds the commercial code paths (verified-publisher admin UI, hosted
runtime, billing) that the OSS proposals deliberately omitted.

## Slim-core target — concrete numbers

| Component | Stays in core | Moves to package |
|---|---|---|
| Bus + signaling | ✅ | |
| Governance (Cat-A/B/C) + oversight queue | ✅ | |
| Cognitive core + agent runtime | ✅ | |
| Perception substrate (registry + profiles) | ✅ | |
| SIP / policy_layer | ✅ | |
| Capability registries (Skill / MCP) | ✅ | |
| Control-plane roles (assistant, arbiter, compliance_officer, orchestrator, observer) | ✅ | |
| OS-basics skill suite (12 skills from v0.3.48) | ✅ (baseline) | |
| `coding_agent` family, `analyst`, `ingester`, `synthesizer` | | `@acc/workspace-roles` |
| `research_*` family (6 roles) | | `@acc/research-roles` |
| Business roles (HR, sales, marketing, ~30 roles) | | `@acc/business-roles` |
| Finance agentset (when it lands) | | `@acc/finance-roles` |
| TUI + webgui | ✅ | |

Target image sizes:

* **`acc-edge`**: ~80 MB = substrate + ONE infused specialist package
* **`acc-standalone`** (default `acc-deploy.sh up`): ~250 MB = core +
  `@acc/workspace-roles` baseline
* **`acc-full`**: ~500 MB = core + 4 standard packages (workspace,
  research, business, governance)

Current v0.3.51 image is ~2.4 GB. The 30× reduction isn't about ACC
code (which is ~1.8 MB compressed); it's about pulling pandas /
lancedb / heavy deps out of the substrate and into per-package
dependencies.

## Business model — five revenue streams (Phase E content)

### Stream 1 — Marketplace transaction fees

15-20% of paid-package revenue. Free packages incur zero fee.
**Year 3-4 target: ~$300k/year.**

### Stream 2 — Verified-Publisher subscription

Annual subscription ($5k-$50k) for `verified` badge + 3-5 business-
day maturity review + SLA-backed maintainer response. Free for
OSS-only publishers. Target customers: Red Hat, Anthropic, vertical
vendors. **Year 2 target: ~$250k/year.**

### Stream 3 — Hosted ACC (SaaS)

Fully-managed runtime at `acc.cloud`. Operator brings LLM key; ACC
provides NATS + Redis + LanceDB + multi-tenant isolation + SSO +
audit retention. Per-agent-hour or per-collective-month pricing.
**Year 2-3 target: ~$600k/year.**

### Stream 4 — Premium role packs

ACC-authored verticals: `@acc-premium/healthcare`,
`@acc-premium/legal`, `@acc-premium/finance`. $50-$500/seat/month
with SOC2 + vertical compliance bundled. **Per-pack target: ~$180k/year.**

### Stream 5 — Support contracts + professional services

Standard ($5k), Enterprise ($50k), Strategic ($500k+) annual support.
Plus professional services (custom roles, migration, training,
certification). Most reliable revenue stream. **Year 2 target:
~$850k/year.**

**Aggregate Year 2-3 target: ~$1-3M ARR** across all five streams.
Not VC-scale; the right scale for "Anthropic-style small-team
self-fund."

## Comparable products + what they teach us

| Comparable | Lesson |
|---|---|
| Hugging Face Hub | Free hub + paid hosted compute. $250M Series D. |
| Docker Hub | Free public + paid private repos. |
| npm + Enterprise npm | Substrate free; enterprise gating drives revenue. |
| VS Code + Marketplace | Free editor; Microsoft monetizes via Azure / Copilot. |
| GitLab Open Core | OSS edition + paid features (EE). IPO at $11B. |
| WordPress.com vs .org | Same code; .com is SaaS + premium themes. |

**Pattern:** substrate stays open + free. Revenue from hosting
(Stream 3) + features-not-shipped-OSS (Streams 2, 4) + trust +
support contracts (Stream 5). Marketplace fees (Stream 1) are
secondary in dollar volume.

## Sequencing diagram

```
v0.3.51 (today)
  capability-pool v1 shipped (50 roles in-tree)
  role-perception-profiles v1 shipped
  assistant-agent-of-agents v1 shipped
       |
       v
Phase A — Role-package format v1
  builder/verifier/installer CLI
  .accpkg manifest schema v1
  coding_agent migration pilot
       |
       v
Phase B — Slim-core
  extract @acc/{workspace,research,business}-roles
  edge image → ~80 MB
       |
       v
Phase C — Hub MVP (static / read-only)
  acc-roles.dev on GitHub Pages / S3
  acc-pkg install @scope/name@version
       |
       v
Phase D — Marketplace UX (Web + TUI)
  acc-web-project /roles publish + install
  TUI Marketplace pane (AoA-P2b queue)
       |
       v
Phase E — Commercial layer (NEW)
  verified-publisher subscription + admin UI
  marketplace transaction layer (Stripe Connect)
  premium pack billing (per-seat)
  hosted runtime MVP
       |
       v
Phase F — Federation
  A2A cross-hub discovery
  private corporate hubs
```

**Critical path:** Phase A unblocks every later phase. Do not start
B, C, or D until A ships.

**Commercial gate:** Phase E does NOT block A-D. Phases A-D can
ship as OSS-only and are the right scope for the next 6 months.
Phase E is when commercial code paths land — likely v0.5.x.

## Cross-references the existing proposals will gain

Once this synthesis lands:

* `20260531-acc-role-package-format` proposal.md will reference
  this strategy as the canonical "why" + "when" anchor.
* `20260603-capability-pool` will gain a Phase 6 note: "Extract
  from core into `@acc/baseline` package" (gated on Phase B).
* The finance-agentset proposal will reference its candidacy as a
  verified-publisher subject.

## Impact

* **Affected code (Phase 1):** none — pure docs.
* **New files:**
  * `docs/architecture/role-ecosystem.md` — operator-facing
    synthesis
  * `openspec/specs/role-ecosystem/strategy.md` — formal sequencing
    + revenue model reference
  * Cross-references in 3 existing proposal.md files
* **New env knobs:** none.
* **Tests:** none (docs only).
* **Backward compatibility:** purely additive.

## What stays open after Phase 1

* The five strategic operator decisions below (each shapes the
  commercial layer).
* Phases A-F implementation — sequenced by this proposal, owned by
  the existing proposals.
* Phase E commercial code (verified-publisher admin, billing,
  hosted runtime) — needs its own follow-up proposals when the
  operator is ready to engage commercial scope.

## Open strategic questions for the operator

1. **OSS license discipline.** Apache 2.0 + CLA gives ACC the right
   to dual-license premium packs. Recommend Apache + CLA.
2. **Hub hosting authority.** Single canonical hub at `acc-roles.dev`
   (Anthropic / Red Hat / foundation-operated), or federated from
   day 1? Recommend single canonical hub for Phase C; federation in
   Phase F.
3. **Verified-Publisher pricing.** Free for OSS-only authors,
   $5-50k/year for commercial vendors. Recommend free-for-OSS.
4. **Phase B trigger.** Slim-core (image extraction) can ship before
   any commercial revenue exists. Sequence as v0.4.0 (a year out)
   or sooner as v0.3.55 (next month)? Recommend v0.4.0 — needs the
   `.accpkg` format mature first.
5. **Phase E ownership.** Commercial code paths probably belong in
   a separate `acc-cloud` repository so the OSS `acc` repo stays
   clean. Confirm split.

## References

* Synthesis brainstorm:
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC-Role-Ecosystem\Role Ecosystem — brainstorm.md`
* Existing brainstorm:
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC-Role-Format\Role package format + community hub — brainstorm.md`
* Naming convention: `openspec/RENAMES.md` (this proposal is
  functional, no `-role-proposal-` infix).
