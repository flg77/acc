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

Each row in this table cites concrete revenue / pricing data —
sources at the bottom of this section and full evidence in
`competitive-analysis.md`.

| Comparable | Headline numbers | Pattern that maps to ACC |
|---|---|---|
| **Hugging Face Hub** | $130M ARR (2024); $235M Series D at $4.5B valuation; Pro $9/mo, Team $20/mo, enterprise custom ([Sacra][cp-hf], [Latka][cp-hf-latka], [TechCrunch][cp-hf-tc]) | Open hub = loss-leader; **enterprise + hosted compute = revenue** |
| **Docker** | Personal free; Pro $9/mo (was $5/mo); Team $15/user/mo; Business $24/user/mo; reversed consumption pricing Feb 2025 ([Docker][cp-dk], [TechTarget][cp-dk-tt], [Docker blog][cp-dk-2024]) | **Tiered subscription works; beware consumption pricing** |
| **npm + GitHub Packages** | Acquired by GitHub (Microsoft) 2020; 1.3M packages, 75B downloads/mo at acquisition ([CNBC][cp-npm], [New Stack][cp-npm-tns]) | Public registry = strategic asset; **private + enterprise audit = recurring revenue** |
| **VS Code Marketplace** | Free verification (6-mo track record + 5-day review); **5% transaction fee** on paid extensions; only ~15% of extensions are paid; solo devs $300-$2,100/mo recurring ([code.visualstudio.com][cp-vs], [markaicode][cp-vs-sell]) | **5%, not 30%** is the modern marketplace fee; transaction revenue is small — value is platform lock-in (Copilot) |
| **GitLab Open Core** | Free / Premium $29/user/mo / Ultimate $99/user/mo; enterprise contracts $3k-$120k/yr ([GitLab][cp-gl], [eesel][cp-gl-eesel]) | **Open core works** when EE features are clearly delineated from the substrate |
| **WordPress.com / Automattic** | $500M-$710M revenue 2024; GPL substrate; recent WP Engine dispute ([Pragmatic Engineer][cp-wp-pe], [appsrhino][cp-wp-apps]) | **Avoid GPL.** WP Engine built a competing hosting business on the GPL substrate — exactly the failure mode |
| **Sentry (BSD → BUSL → FSL)** | Moved BSD→BUSL (2019)→Functional Source License (2023) to prevent hyperscaler free-riding; FSL converts to Apache 2.0 after 2 years ([Sentry blog][cp-sentry-fsl], [InfoQ][cp-sentry-infoq], [TechCrunch][cp-fair-tc]) | **FSL is the modern fallback** if hyperscaler clones the substrate; not the starting point |
| **MCP Registry (Anthropic-led)** | Launched Sep 2025; 0 → ~10,000 servers in 12 months (407% MoM growth); canonical hub + public/private subregistries ([MCP blog][cp-mcp], [MCP anniversary][cp-mcp-anniv], [digitalapplied][cp-mcp-stats]) | **Direct precedent for ACC's hub architecture** |
| **Red Hat Partner Validation** | Free at the program level; Partner Validation = self-verified; Partner Certification = deeper testing; tech resources subsidized ([Red Hat Connect][cp-rh], [docs.redhat.com][cp-rh-docs]) | **Two-tier verification (free entry + paid depth) works at enterprise scale** |
| **Tidelift (now Sonar-owned)** | $10k/year guaranteed minimum per pre-approved package; payments based on SBOM-to-customer attribution; recently acquired by Sonar ([dev.to][cp-tide], [PR Newswire][cp-tide-pr], [Tidelift support][cp-tide-pay], [Socket][cp-tide-sonar]) | **Demand-side maintainer payments** are a viable Stream 6; SBOM attribution math is reusable |

**Pattern across all of these:** the substrate stays open + free.
Revenue comes from **hosting** (Stream 3) + **features not shipped
OSS** (Streams 2, 4) + **trust + support contracts** (Stream 5).
Marketplace transaction fees (Stream 1) are real but secondary in
dollar volume — VS Code's 5% on a market where only 15% of
extensions monetize at all is the realistic ceiling.

[cp-hf]: https://sacra.com/c/hugging-face/
[cp-hf-latka]: https://getlatka.com/companies/hugging-face
[cp-hf-tc]: https://techcrunch.com/2023/08/24/hugging-face-raises-235m-from-investors-including-salesforce-and-nvidia/
[cp-dk]: https://www.docker.com/pricing/
[cp-dk-2024]: https://www.docker.com/blog/november-2024-updated-plans-announcement/
[cp-dk-tt]: https://www.techtarget.com/searchsoftwarequality/news/366610229/Docker-pricing-changes-hike-midtier-costs
[cp-npm]: https://www.cnbc.com/2020/03/16/microsoft-github-agrees-to-buy-code-distribution-start-up-npm.html
[cp-npm-tns]: https://thenewstack.io/github-acquires-npm-buying-microsoft-a-presence-in-the-node-javascript-community/
[cp-vs]: https://code.visualstudio.com/api/working-with-extensions/publishing-extension
[cp-vs-sell]: https://markaicode.com/sell-vs-code-extensions-2025/
[cp-gl]: https://about.gitlab.com/pricing/
[cp-gl-eesel]: https://www.eesel.ai/blog/gitlab-pricing
[cp-wp-pe]: https://blog.pragmaticengineer.com/wordpress-struggles/
[cp-wp-apps]: https://www.appsrhino.com/blogs/business-model-of-wordpress-complete-guide
[cp-sentry-fsl]: https://blog.sentry.io/sentry-is-now-fair-source/
[cp-sentry-infoq]: https://www.infoq.com/news/2023/12/functional-source-license/
[cp-fair-tc]: https://techcrunch.com/2024/09/22/some-startups-are-going-fair-source-to-avoid-the-pitfalls-of-open-source-licensing/
[cp-mcp]: https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/
[cp-mcp-anniv]: https://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/
[cp-mcp-stats]: https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol
[cp-rh]: https://connect.redhat.com/en/blog/announcing-partner-validation-new-entry-point-red-hat-ecosystem
[cp-rh-docs]: https://docs.redhat.com/en/documentation/red_hat_software_certification/2025/html-single/red_hat_enterprise_linux_software_certification_policy_guide/index
[cp-tide]: https://dev.to/tidelift/1m-to-pay-open-source-maintainers-on-tidelift-294m
[cp-tide-pr]: https://www.prnewswire.com/news-releases/tidelift-reaches-milestone-of-one-million-dollars-committed-to-pay-open-source-software-maintainers-300713996.html
[cp-tide-pay]: https://support.tidelift.com/hc/en-us/articles/4406294816916-How-we-pay-lifters
[cp-tide-sonar]: https://socket.dev/blog/sonar-to-acquire-tidelift

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

## Strategic decisions — answered with citations

The v0.3.52 ship left five strategic questions open. v0.3.53 (this
revision) answers each with citation-backed reasoning. Full evidence
in `competitive-analysis.md`; per-decision rationale in
`strategic-decisions.md`.

| # | Question | Decision | Confidence |
|---|---|---|---|
| Q1 | OSS license discipline | **Apache 2.0 + CLA** | High |
| Q2 | Hub hosting authority | **Single canonical hub + public / private subregistries** | High |
| Q3 | Verified-Publisher pricing | **Free Community / $5k Standard / $25k Premium tiers** | Medium |
| Q4 | Phase B (slim-core) trigger | **v0.4.0 target (Q3 2026)** | Medium |
| Q5 | Phase E ownership | **Separate `flg77/acc-cloud` private repo** | High |

### Q1 — Apache 2.0 + CLA

Apache's explicit patent grant is what enterprise legal teams
require ([Apache.org][p-apache]) ([FOSSA][p-fossa]). A CLA preserves
the right to relicense if business conditions change (Sentry's
BSD → BUSL → FSL trajectory in 2024 — [Sentry blog][p-sentry-fsl]).
WordPress's GPL trap with WP Engine
([Pragmatic Engineer][p-wp-pe]) shows why permissive-without-CLA is
exposed. HuggingFace (~$130M ARR at $4.5B valuation —
[Sacra][p-hf]; [TechCrunch][p-hf-tc]), GitLab, npm + Microsoft
([CNBC][p-npm-cnbc]) all run on the Apache-or-permissive + CLA
pattern.

### Q2 — Single canonical hub

The MCP Registry's architecture (launched Sep 2025) is the
freshest direct comparable: canonical hub at
`registry.modelcontextprotocol.io` + public subregistries
("opinionated marketplaces" per client) + private corporate
subregistries ([MCP blog][p-mcp]; [anniversary post][p-mcp-anniv]).
0 → 10,000 servers in 12 months ([digitalapplied][p-mcp-stats])
proves the canonical-hub model doesn't bottleneck adoption. npm,
PyPI, Docker Hub, VS Code all run canonical-first. Federation
(Phase F) handles cross-hub discovery later.

### Q3 — Three-tier pricing

* **Community Verified — free.** Copies VS Code Marketplace
  (6-month track record + 5-business-day review, no fee —
  [code.visualstudio.com][p-vs]) and Red Hat Partner Validation
  (free at the program level — [Red Hat Connect][p-rh]).
* **Standard — $5k/year.** Director-level discretionary budget;
  SLA-backed maintainer response; priority review.
* **Premium — $25k/year.** VP-level signoff; dedicated channel;
  cross-promotion in release notes.

Tidelift pays maintainers $10k/year minimum per package
([dev.to][p-tide-blog]) — that's the demand-side inverse and
becomes Stream 6 in a future revision.

### Q4 — v0.4.0 in Q3 2026

Phase A (`.accpkg` format) must ratify before Phase B (extraction)
can run without breaking every operator. Sigstore + cosign is
"afternoon work" today ([OpenSSF][p-cosign]) — the bottleneck is
operator migration cost, not crypto plumbing. Re-trigger sooner
only if hyperscaler clones the substrate or an edge customer signs
a sub-200 MB image requirement.

### Q5 — Separate `acc-cloud` private repo

Sentry's split (OSS SDKs vs FSL primary repo —
[Sentry blog][p-sentry-fsl]) and Docker's split (Apache 2.0
`moby/moby` vs proprietary Docker Inc products) are the clean
precedents. GitLab's CE+EE-in-one-repo pattern
([about.gitlab.com][p-gl]) works but creates contamination
grey-zones on every PR. Take the clean split: `flg77/acc`
(Apache 2.0 + CLA) + `flg77/acc-cloud` (proprietary, private).

## References

* Synthesis brainstorm:
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC-Role-Ecosystem\Role Ecosystem — brainstorm.md`
* Existing brainstorm:
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC-Role-Format\Role package format + community hub — brainstorm.md`
* Competitive evidence: `competitive-analysis.md` (sibling)
* Per-decision reasoning: `strategic-decisions.md` (sibling)
* Naming convention: `openspec/RENAMES.md` (this proposal is
  functional, no `-role-proposal-` infix).

[p-apache]: https://www.apache.org/licenses/LICENSE-2.0
[p-fossa]: https://fossa.com/blog/open-source-licenses-101-apache-license-2-0/
[p-sentry-fsl]: https://blog.sentry.io/sentry-is-now-fair-source/
[p-wp-pe]: https://blog.pragmaticengineer.com/wordpress-struggles/
[p-hf]: https://sacra.com/c/hugging-face/
[p-hf-tc]: https://techcrunch.com/2023/08/24/hugging-face-raises-235m-from-investors-including-salesforce-and-nvidia/
[p-npm-cnbc]: https://www.cnbc.com/2020/03/16/microsoft-github-agrees-to-buy-code-distribution-start-up-npm.html
[p-mcp]: https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/
[p-mcp-anniv]: https://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/
[p-mcp-stats]: https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol
[p-vs]: https://code.visualstudio.com/api/working-with-extensions/publishing-extension
[p-rh]: https://connect.redhat.com/en/blog/announcing-partner-validation-new-entry-point-red-hat-ecosystem
[p-tide-blog]: https://dev.to/tidelift/1m-to-pay-open-source-maintainers-on-tidelift-294m
[p-cosign]: https://openssf.org/blog/2024/02/16/scaling-up-supply-chain-security-implementing-sigstore-for-seamless-container-image-signing/
[p-gl]: https://about.gitlab.com/pricing/
