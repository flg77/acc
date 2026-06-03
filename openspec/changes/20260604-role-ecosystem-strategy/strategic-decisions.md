# Strategic decisions — answered with evidence

Each of the five open questions from `proposal.md`, now answered
with citation-backed reasoning. All references trace to
`competitive-analysis.md` in this folder.

## Decision matrix at a glance

| # | Question | Decision | Confidence | Triggers re-eval |
|---|---|---|---|---|
| Q1 | OSS license discipline | **Apache 2.0 + CLA** | High | Hyperscaler clone of substrate → consider FSL (Sentry pattern) |
| Q2 | Hub hosting authority | **Single canonical hub + public/private subregistries** | High | None foreseeable |
| Q3 | Verified-Publisher pricing | **Free OSS tier + $5k / $25k commercial tiers** | Medium | First 10 publishers' behaviour |
| Q4 | Phase B (slim-core) trigger | **v0.4.0 (Q3 2026 target)** | Medium | Phase A maturity + first 3 packaged roles |
| Q5 | Phase E ownership | **Separate `flg77/acc-cloud` private repo** | High | None foreseeable |

---

## Q1 — OSS license discipline

**Decision: Apache 2.0 + Contributor License Agreement (CLA).**

### Why

Three properties matter for ACC's substrate:

1. **Patent grant.** Apache 2.0 carries an explicit patent grant
   that enterprise legal teams require before any production
   deployment ([FOSSA][f-fossa]). MIT does not.
2. **Re-licensing right.** A CLA gives the project maintainers
   (ACC) the unilateral right to dual-license contributions
   under a different licence if business conditions change —
   for example, moving to FSL the way Sentry did
   ([Sentry blog][f-sentry-fsl]). Without a CLA, every
   contributor must individually consent.
3. **Permissive ≠ exposed.** Apache 2.0 explicitly allows
   commercial use ([Apache.org][f-apache-license])
   ([FOSSA][f-fossa]). The pattern of building proprietary
   premium features on top of an Apache 2.0 base is the
   industry standard — GitLab CE/EE, Hugging Face hub vs
   hosted compute ([sacra.com][f-hf-sacra]), npm public vs
   Enterprise ([CNBC][f-npm-cnbc]), Docker Engine vs Docker
   Inc commercial products ([Docker pricing][f-docker]).

### Why not GPL

WordPress is GPL. WP Engine, a third party, built a $400M+ hosting
business directly on WordPress and is now in an active dispute with
Automattic ([Pragmatic Engineer][f-wp-pe]). GPL does **not** prevent
hyperscalers from cloning the substrate and selling hosting; it just
forces them to keep modifications open. That's not the protection
ACC needs.

### Why not FSL / BUSL today

Sentry moved to FSL in 2024 to prevent free-riding by AWS-style
hyperscalers ([Sentry blog][f-sentry-fsl])
([InfoQ][f-sentry-infoq]). FSL is non-OSI-approved but converts
to Apache 2.0 after 2 years (Delayed Open Source Publication)
([TechCrunch][f-fair-tc]). This is the right answer **once a
hyperscaler clones ACC**, but premature today — it would lock out
the OSS publisher community before the network effect exists.

### Implementation

* `LICENSE` file: Apache 2.0 (already in place).
* `CLA.md`: NEW — adopt the EasyCLA template
  ([cla.linuxfoundation.org pattern]).
* `CONTRIBUTING.md`: NEW — explain CLA + DCO requirement.
* GitHub Actions check: `cla-assistant/github-action` to gate
  PRs on CLA signature.

---

## Q2 — Hub hosting authority

**Decision: Single canonical hub at `acc-roles.dev` operated by
the project (foundation-style governance), with public and
private subregistry support from day 1.**

### Why

The MCP Registry's launch in September 2025 is the freshest
direct comparable ([MCP blog][f-mcp-blog]) and validates the
architecture:

* **Canonical hub** at `registry.modelcontextprotocol.io`
  ([MCP blog][f-mcp-blog]).
* **Public subregistries** ("opinionated MCP marketplaces"
  per client like ChatGPT / Cursor) are free to augment data
  from the upstream registry
  ([MCP anniversary post][f-mcp-anniv]).
* **Private subregistries** inside enterprises for privacy +
  security requirements ([MCP anniversary post][f-mcp-anniv]).
* **Growth**: 0 → 10,000 active servers in ~12 months
  ([digitalapplied][f-mcp-stats]). The canonical-hub model
  doesn't bottleneck adoption.

Other comparables agree on canonical-first:

* npm: single hub, Microsoft-acquired
  ([CNBC][f-npm-cnbc]).
* PyPI: single hub, PSF-operated.
* Docker Hub: single hub, Docker Inc.-operated.
* VS Code Marketplace: single hub, Microsoft-operated
  ([code.visualstudio.com][f-vs-publishing]).

**Federation** (Phase F in proposal.md) is the right answer for
**discovery across multiple canonical hubs**, not for replacing
the canonical hub at the substrate level.

### Implementation sequencing

| Phase | Hub state |
|---|---|
| Phase A (v0.4.0) | Local-file artifacts only; no hub yet |
| Phase C (~v0.4.5) | `acc-roles.dev` static read-only on GitHub Pages / S3; manifest index + signature verification |
| Phase D (~v0.5.0) | Publish API, ratings, search, verified-publisher tier |
| Phase F (~v0.6.0+) | Federation: private hubs mirror canonical; canonical can federate with other roots if scale demands |

### Governance

The canonical hub needs neutral governance to avoid hyperscaler
capture. Options, ranked by realism:

1. **ACC project owns it via the BDFL / core-maintainer team**
   — simplest, most realistic for v0.x. Pattern: PyPI's relationship
   to the PSF.
2. Foundation-sponsored (Linux Foundation, OpenSSF, CNCF) —
   right answer at v1.0+ when contribution scale demands it.
3. Anthropic / Red Hat operates as steward — fast bootstrap;
   risk of single-vendor capture.

Recommend **option 1 for Phase C-D**; revisit option 2 at v1.0.

---

## Q3 — Verified-Publisher pricing

**Decision: Three tiers.**

| Tier | Cost | Eligibility | Benefits |
|---|---|---|---|
| **Community Verified** | Free | ≥ 6 months track record on the hub; ≥ 1 stable-tier package; clean Cat-C verdict history | Verified badge; 5-business-day review SLA |
| **Standard** | $5,000/year | Commercial vendor; 1 named maintainer with SLA on critical bugs | Above + priority package review (1 business day); SLA-backed maintainer response; verified-vendor logo on marketplace |
| **Premium** | $25,000/year | Vertical / enterprise vendor (healthcare, finance, legal); 24/7 maintainer pager | Above + dedicated maintainer review channel; cross-promotion in ACC release notes; sponsorship line in `acc-deploy.sh` MOTD |

### Why these numbers

* **VS Code Marketplace publisher verification is free** with a
  6-month track record + 5-business-day review
  ([code.visualstudio.com][f-vs-publishing]). The Community
  Verified tier copies this baseline.
* **Red Hat Partner Validation is free** at the program level;
  Red Hat subsidizes the technical resources
  ([Red Hat Connect][f-rh-blog]). This proves "free entry, paid
  certification depth" works for enterprise-grade vendors.
* **$5k/year Standard** matches the entry-level pricing for
  comparable Red Hat ISV partnership programs (varies; the
  point is it's "annual SaaS subscription" pricing, not
  per-transaction).
* **$25k/year Premium** aligns with how vertical-SaaS vendors
  budget marketplace presence today — sub-$50k is approval
  authority for a director-level marketing budget; over $50k
  requires VP sign-off.

### Why not Tidelift-style $10k/package floor

Tidelift pays maintainers a $10k/year guaranteed minimum per
package ([dev.to][f-tide-blog]). That's a *demand-side* model:
enterprise subscribers fund maintainers via SBOM attribution.

ACC's Q3 is a *supply-side* question — what publishers pay TO
the platform. Wrong direction.

But: **ACC should add Tidelift-style maintainer payments as a
separate Stream 6** in a future revenue-model revision. The
SBOM-to-attribution math ([Tidelift][f-tide-pay]) is directly
reusable.

---

## Q4 — Phase B (slim-core) trigger

**Decision: v0.4.0 target, Q3 2026.**

### Why v0.4.0 and not sooner

Phase B extracts roles from in-tree to packaged. That requires:

1. **`.accpkg` format ratified** (Phase A complete). Without a
   stable format, extracted roles are unwound packages no-one
   can install. Phase A is the critical path.
2. **At least 3 roles successfully packaged** as Phase A pilots
   so the migration pattern is proven. `coding_agent` is the
   first pilot per
   `20260531-acc-role-package-format` Phase 2.
3. **Hub MVP at least read-only** (Phase C). Operators need
   somewhere to fetch the extracted packages from. Without a
   hub, slim core = broken core.

### Timeline math

| Quarter | Milestone | Version |
|---|---|---|
| Q2 2026 (today) | v0.3.52 — strategy ratified | v0.3.52 |
| Q3 2026 | Phase A — `.accpkg` v1 + builder/verifier/installer + `coding_agent` pilot | v0.4.0 |
| Q4 2026 | Phase B start — extract `@acc/workspace-roles` + Phase C MVP (`acc-roles.dev` read-only) | v0.4.5 |
| Q1 2027 | Phase B complete — `@acc/{research,business}-roles` extracted; edge image ~80 MB target | v0.5.0 |
| Q2 2027 | Phase D — TUI Marketplace pane + acc-web-project /roles publish | v0.5.5 |
| Q3-Q4 2027 | Phase E — commercial layer in `acc-cloud` repo | (acc-cloud v0.1) |

### Why not sooner

* Sigstore + cosign setup is now an afternoon's work
  ([OpenSSF][f-cosign-openssf]). Time-to-implement isn't the
  bottleneck.
* The real bottleneck is the **migration tax** on every existing
  operator. Phases A and C must land before Phase B can run
  without breaking lighthouse + every operator who's deployed
  v0.3.x.
* Mid-stream slim-core would cause exactly the v0.3.48 problem:
  shipping 50 in-tree role flips while Phase B says
  "extract them all" — wasted engineering.

### Re-trigger conditions

Re-evaluate Phase B sooner IF:

* Hyperscaler clones the substrate (forces Sentry-style FSL
  pivot, which would change everything).
* Edge customer signs a contract requiring < 200 MB image NOW
  (then a stop-gap shrink is more important than format
  perfection).
* `.accpkg` Phase A slips by > 1 quarter (rebaseline everything).

---

## Q5 — Phase E ownership (separate repo)

**Decision: Split. Two repositories.**

| Repo | License | Visibility | Contains |
|---|---|---|---|
| `flg77/acc` (current) | Apache 2.0 + CLA | Public | OSS substrate, `.accpkg` format spec, `acc-deploy.sh`, TUI, webgui (auth substrate), standard packages |
| `flg77/acc-cloud` (NEW) | Proprietary | Private | Verified-Publisher admin, Stripe Connect billing, hosted runtime, premium-pack publishing tools, SSO/audit add-ons |

### Why split

* **License hygiene.** Apache 2.0 + proprietary code in the same
  repo invites contamination accidents. Sentry split this way
  (OSS SDKs vs FSL primary repo)
  ([Sentry blog][f-sentry-fsl]).
* **CLA scope.** Contributors to `acc` sign the CLA; they don't
  see (or sign anything against) `acc-cloud`. Clear consent
  boundary.
* **Different release cadences.** OSS substrate ships continuously
  (52 releases in 60 days). Commercial layer follows enterprise
  cadence (quarterly release trains).
* **GitLab counter-example.** GitLab keeps CE + EE in one repo
  via `/ee/` subdir ([gitlab.com][f-gl-pricing]). This works
  but creates legal grey-zones every time someone files a PR.
  Avoid the pattern.
* **Docker pattern.** Docker Engine (Apache 2.0, in
  `moby/moby`) is fully separate from Docker Inc commercial
  products. Clean precedent.

### Implementation

* **Today**: nothing — `acc-cloud` doesn't exist yet.
* **Phase E start (~Q3 2027)**: create `flg77/acc-cloud` as a
  private repo. Use the same monorepo conventions as `acc`
  (Python packages, pytest, openspec/).
* **Cross-references**: `acc-cloud` depends on `acc` as an
  upstream dependency. `acc` never depends on `acc-cloud`.
* **CI**: separate workflows; ghcr.io publishing under
  `ghcr.io/flg77/acc-cloud-*` for commercial images.

---

## Citation summary

[f-apache-license]: https://www.apache.org/licenses/LICENSE-2.0
[f-fossa]: https://fossa.com/blog/open-source-licenses-101-apache-license-2-0/

[f-hf-sacra]: https://sacra.com/c/hugging-face/

[f-npm-cnbc]: https://www.cnbc.com/2020/03/16/microsoft-github-agrees-to-buy-code-distribution-start-up-npm.html

[f-docker]: https://www.docker.com/pricing/

[f-wp-pe]: https://blog.pragmaticengineer.com/wordpress-struggles/

[f-sentry-fsl]: https://blog.sentry.io/sentry-is-now-fair-source/
[f-sentry-infoq]: https://www.infoq.com/news/2023/12/functional-source-license/
[f-fair-tc]: https://techcrunch.com/2024/09/22/some-startups-are-going-fair-source-to-avoid-the-pitfalls-of-open-source-licensing/

[f-mcp-blog]: https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/
[f-mcp-anniv]: https://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/
[f-mcp-stats]: https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol

[f-vs-publishing]: https://code.visualstudio.com/api/working-with-extensions/publishing-extension

[f-rh-blog]: https://connect.redhat.com/en/blog/announcing-partner-validation-new-entry-point-red-hat-ecosystem

[f-tide-blog]: https://dev.to/tidelift/1m-to-pay-open-source-maintainers-on-tidelift-294m
[f-tide-pay]: https://support.tidelift.com/hc/en-us/articles/4406294816916-How-we-pay-lifters

[f-cosign-openssf]: https://openssf.org/blog/2024/02/16/scaling-up-supply-chain-security-implementing-sigstore-for-seamless-container-image-signing/

[f-gl-pricing]: https://about.gitlab.com/pricing/
