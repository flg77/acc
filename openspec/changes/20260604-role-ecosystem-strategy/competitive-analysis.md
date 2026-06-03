# Competitive analysis — cited

Reference data for the role-ecosystem strategy. Every claim in
`proposal.md` traces back to one of these sources.

## Comparable platforms — what they monetized, what they didn't

### Hugging Face — open hub + paid hosted compute

* **Revenue 2024**: ~$130M ARR ([getlatka.com][hf-latka])
  ([Contrary Research][hf-contrary]).
* **Funding**: $235M Series D (Aug 2023) at $4.5B valuation —
  ~100× annualized revenue at the time
  ([TechCrunch][hf-techcrunch]).
* **Investors**: Google, Amazon, Nvidia, Intel, AMD, Qualcomm, IBM,
  Salesforce, Sound Ventures ([TechCrunch][hf-techcrunch]).
* **Monetization mix**:
  * Pro: $9/mo per individual ([sacra.com][hf-sacra])
  * Team: $20/mo per individual ([sacra.com][hf-sacra])
  * Enterprise: custom SSO, audit logs, private cloud / on-prem
    deployments — the majority of revenue
    ([sacra.com][hf-sacra])
  * Shifting toward API usage + cloud-referral recurring revenue
    ([sacra.com][hf-sacra])

**Lesson for ACC**: open hub is the loss-leader; enterprise +
hosted compute is the revenue. The hub itself doesn't have to
profit directly.

### GitLab — open core with feature-gated EE

* **Pricing**: Free / Premium ($29/user/mo) / Ultimate ($99/user/mo)
  ([gitlab.com][gl-pricing]).
* **Enterprise customers**: $3k-$120k annual contract range
  ([eesel.ai][gl-eesel]).
* **Pattern**: same codebase, EE features gated by flag/license.
  CE is MIT; EE add-ons are proprietary.

**Lesson for ACC**: open core works when the substrate is genuinely
useful for free users AND enterprise-only features are clearly
delineated. Don't try to gate the substrate itself.

### Docker Inc — substrate + tiered subscription

* **Pricing 2025**:
  * Personal: free for individuals / small business / OSS
    ([docker.com][dk-pricing]) ([spendflo.com][dk-spendflo])
  * Pro: $9/mo (up from $5/mo)
    ([docker.com blog][dk-blog2024])
  * Team: $15/user/mo (up from $9/user/mo)
    ([docker.com blog][dk-blog2024])
  * Business: $24/user/mo
    ([docker.com blog][dk-blog2024])
* **Reversed consumption pricing** Feb 2025 — guaranteed unlimited
  pulls for paid tiers ([techtarget.com][dk-techtarget]).

**Lesson for ACC**: tiered subscription is the workhorse. Free for
individuals/OSS preserves the network effect. Beware
consumption-based pricing — Docker reversed it inside a year.

### npm + GitHub Packages — acquisition for ecosystem lock-in

* **Acquisition 2020**: GitHub (Microsoft) bought npm Inc — price
  undisclosed ([CNBC][npm-cnbc])
  ([The New Stack][npm-tns]).
* **At acquisition**: 1.3M packages, 75B downloads/month
  ([thurrott.com][npm-thurrott]).
* **Commitment**: public registry stays free + open-source
  forever; npm Pro / Teams / Enterprise (private registries +
  enterprise audit) are paid ([CNBC][npm-cnbc]).

**Lesson for ACC**: the public registry is a strategic asset
worth acquiring; private registry + enterprise audit is where
the recurring revenue lives.

### VS Code Marketplace — free hub, transaction fee on paid

* **Free verification** for publishers with ≥ 6 months track record +
  domain ≥ 6 months old; 5-business-day review
  ([code.visualstudio.com][vs-publishing]).
* **No built-in payment surface** — Microsoft doesn't charge for
  free extensions; commercial extensions sell outside Marketplace
  ([markaicode.com][vs-sell])
  ([dodopayments.com][vs-dodo]).
* **5% transaction fee** when extensions ARE sold through
  Marketplace + standard 2.9% + $0.30 payment processor
  ([markaicode.com][vs-sell]).
* **Solo developer revenue**: $300-$2,100/mo recurring for well-
  maintained extensions; only ~15% of extensions are paid
  ([markaicode.com][vs-sell]).

**Lesson for ACC**: 5% (not 30%) on paid extensions is the modern
SaaS-aligned norm. Most marketplaces won't have material
transaction revenue — the value is the platform lock-in for the
hosting business (Azure / Copilot in VS Code's case).

### WordPress.com / Automattic — open core with GPL tension

* **Revenue 2024**: $500M+ — one source says $710M (+11.2% YoY)
  ([appsrhino.com][wp-apps])
  ([wpindigo.com][wp-indigo]).
* **Monetization**: managed hosting + premium themes/plugins +
  WooCommerce ([intelivita.com][wp-inteli]).
* **License tension**: WordPress is GPL; recent WP Engine dispute
  shows GPL doesn't prevent third parties from building rival
  hosting businesses on top
  ([pragmaticengineer.com][wp-pe]).

**Lesson for ACC**: avoid GPL. WordPress's permissive license is
exactly why WP Engine exists; that's the failure mode. Apache 2.0
+ CLA keeps the patent grant + re-license option.

### Sentry — moved from BSD to FSL to prevent free-riding

* **2019**: BSD 3-Clause → BUSL (Business Source License)
  ([blog.sentry.io][sentry-blog]).
* **2024**: BUSL → **Functional Source License (FSL)** — non-
  compete for 2 years, then converts to Apache 2.0 or MIT
  ([blog.sentry.io][sentry-fsl])
  ([InfoQ][sentry-infoq]).
* **FSL is NOT OSI-approved** but designed to be "Fair Source"
  with delayed open-source publication
  ([TechCrunch][fair-tc]).
* **Companions in fair-source movement**: Keygen (FCL), MariaDB
  (BSL) ([startupnews.fyi][fair-news]).

**Lesson for ACC**: FSL is the modern answer when hyperscaler
free-riding becomes a real threat. Premature for ACC v0.x — but
keep it as the v1.x fallback if Anthropic / AWS clone the
substrate. Apache 2.0 + CLA gives ACC the unilateral right to
relicense if needed.

## MCP Registry — the canonical reference

The MCP Registry's structure (launched Sep 2025) is the **closest
direct comparable** to what ACC's hub needs to be:

* **Launch**: September 2025; ~2,000 entries by Nov 2025 (407%
  MoM growth) ([modelcontextprotocol.io][mcp-blog])
  ([digitalapplied.com][mcp-stats]).
* **Architecture**: canonical hub at
  `registry.modelcontextprotocol.io`; public subregistries
  ("opinionated MCP marketplaces" per client) free to augment;
  private subregistries inside enterprises
  ([modelcontextprotocol.io][mcp-spec]).
* **Adoption Dec 2025**: 10,000+ active public servers; clients
  include ChatGPT, Cursor, Gemini, Microsoft Copilot, VS Code
  ([digitalapplied.com][mcp-stats]).

**Lesson for ACC**: copy this architecture exactly. Canonical hub
at `acc-roles.dev`; private corporate hubs allowed; client-side
subregistries allowed. Federation via the published index spec.

## Red Hat Partner Validation — verified-publisher comparable

* **Partner Validation**: self-verified compatibility statement;
  no certification fee; vendor submits documentation
  ([connect.redhat.com][rh-blog]).
* **Partner Certification**: deeper — testing, interoperability,
  security, lifecycle requirements; collaborative support
  ([docs.redhat.com][rh-cert-policy]).
* **Differentiator**: Validation is the easy entry point;
  Certification is the trusted tier. Both free at the program
  level (Red Hat subsidizes; commercial vendors get tech
  resources gratis) ([connect.redhat.com][rh-blog]).

**Lesson for ACC**: two-tier verification works. Use Validation
for the OSS community (low barrier) and Certification for the
commercial vendors (deeper review, paid). Red Hat itself is the
canonical "verified-publisher" candidate for RHOAI packages.

## Tidelift — paying maintainers via SBOM-based attribution

* **Pre-approved packages**: $10k/year minimum guaranteed for
  selected packages; no cap on earnings
  ([dev.to/tidelift][tide-blog]) ([prnewswire][tide-pr]).
* **Distribution**: SBOM analysis from customers → income
  distributed monthly based on usage + strategic importance
  ([tidelift.com][tide-pay]).
* **Recently acquired** by Sonar — proving the maintainer-funding
  model has value to enterprise security platforms
  ([socket.dev][tide-sonar]).

**Lesson for ACC**: there's an existing market for paying OSS
maintainers via enterprise subscription routing. ACC's verified-
publisher tier should learn from Tidelift's SBOM-to-payment
attribution model.

## Sigstore + cosign — modern signing infrastructure

* **Stack**: Cosign (CLI) + Fulcio (CA) + Rekor (transparency
  log) + OIDC providers (Microsoft, Google, GitHub, GitLab,
  CircleCI) ([sigstore.dev][cosign-overview]).
* **Adoption**: NPM, PyPI, Maven, GitHub, Homebrew, Kubernetes
  ([sigstore.dev][cosign-overview]).
* **Setup cost 2025**: "afternoon to achieve SLSA Level 2"
  thanks to GitHub's built-in attestation +
  `slsa-github-generator` ([openssf.org][cosign-openssf]).

**Lesson for ACC**: the trust-model substrate is mature. No need
to build a CA infrastructure; pin to Sigstore. Time-to-implement
is hours, not weeks.

---

## Sources

[hf-latka]: https://getlatka.com/companies/hugging-face
[hf-contrary]: https://research.contrary.com/report/hugging-face
[hf-techcrunch]: https://techcrunch.com/2023/08/24/hugging-face-raises-235m-from-investors-including-salesforce-and-nvidia/
[hf-sacra]: https://sacra.com/c/hugging-face/

[gl-pricing]: https://about.gitlab.com/pricing/
[gl-eesel]: https://www.eesel.ai/blog/gitlab-pricing

[dk-pricing]: https://www.docker.com/pricing/
[dk-spendflo]: https://www.spendflo.com/blog/docker-pricing-guide
[dk-blog2024]: https://www.docker.com/blog/november-2024-updated-plans-announcement/
[dk-techtarget]: https://www.techtarget.com/searchsoftwarequality/news/366610229/Docker-pricing-changes-hike-midtier-costs

[npm-cnbc]: https://www.cnbc.com/2020/03/16/microsoft-github-agrees-to-buy-code-distribution-start-up-npm.html
[npm-tns]: https://thenewstack.io/github-acquires-npm-buying-microsoft-a-presence-in-the-node-javascript-community/
[npm-thurrott]: https://www.thurrott.com/dev/232551/microsofts-github-acquires-npm

[vs-publishing]: https://code.visualstudio.com/api/working-with-extensions/publishing-extension
[vs-sell]: https://markaicode.com/sell-vs-code-extensions-2025/
[vs-dodo]: https://dodopayments.com/blogs/sell-vscode-extensions

[wp-apps]: https://www.appsrhino.com/blogs/business-model-of-wordpress-complete-guide
[wp-indigo]: https://wpindigo.com/who-owns-wordpress/
[wp-inteli]: https://www.intelivita.com/blog/how-does-wordpress-make-money/
[wp-pe]: https://blog.pragmaticengineer.com/wordpress-struggles/

[sentry-blog]: https://blog.sentry.io/sentry-is-now-fair-source/
[sentry-fsl]: https://blog.sentry.io/introducing-the-functional-source-license-freedom-without-free-riding/
[sentry-infoq]: https://www.infoq.com/news/2023/12/functional-source-license/
[fair-tc]: https://techcrunch.com/2024/09/22/some-startups-are-going-fair-source-to-avoid-the-pitfalls-of-open-source-licensing/
[fair-news]: https://startupnews.fyi/2024/09/22/some-startups-are-going-fair-source-to-avoid-the-pitfalls-of-open-source-licensing/

[mcp-blog]: https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/
[mcp-stats]: https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol
[mcp-spec]: https://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/

[rh-blog]: https://connect.redhat.com/en/blog/announcing-partner-validation-new-entry-point-red-hat-ecosystem
[rh-cert-policy]: https://docs.redhat.com/en/documentation/red_hat_software_certification/2025/html-single/red_hat_enterprise_linux_software_certification_policy_guide/index

[tide-blog]: https://dev.to/tidelift/1m-to-pay-open-source-maintainers-on-tidelift-294m
[tide-pr]: https://www.prnewswire.com/news-releases/tidelift-reaches-milestone-of-one-million-dollars-committed-to-pay-open-source-software-maintainers-300713996.html
[tide-pay]: https://support.tidelift.com/hc/en-us/articles/4406294816916-How-we-pay-lifters
[tide-sonar]: https://socket.dev/blog/sonar-to-acquire-tidelift

[cosign-overview]: https://docs.sigstore.dev/cosign/signing/overview/
[cosign-openssf]: https://openssf.org/blog/2024/02/16/scaling-up-supply-chain-security-implementing-sigstore-for-seamless-container-image-signing/
