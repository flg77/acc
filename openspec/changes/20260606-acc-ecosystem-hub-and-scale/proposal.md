# 20260606-acc-ecosystem-hub-and-scale — proposal

## Why

Stage 0 (PR #20) + Stage 1 (PRs #21-#33) shipped the substrate:
`acc-pkg` CLI, catalog format with dual-source loader, OIDC keyless
publish, Enterprise Contract policy, operator CRDs + reconcilers.
All packages today live in this repo's `roles/` tree and are
served via the internal `acc1` K8s hub.  The architecture's promise
of a public ecosystem (brainstorm `<vault>/ACC Openspec/ACC Role
Ecosystem/Ecosystem split — brainstorm.md`) requires three moves
the operator deferred to Stage 2:

1. **Repo split** — extract the 44 movable roles into
   `flg77/acc-ecosystem` (public, Apache 2.0 + CLA).  The 7
   CONTROL roles (arbiter, assistant, compliance_officer, ingester,
   observer, orchestrator, reviewer) stay in `acc`.
2. **Public hub** — promote the acc1 internal endpoint to a public
   read-only mirror at `acc-roles.dev`.  Stage 1.3's OIDC keyless
   publish path then serves community publishers.
3. **Discovery surfaces** — Marketplace + Catalog admin panes in
   the TUI + WebGUI parity, plus the `acc-podman-desktop` plugin
   per brainstorm Q7 (consumer-edge curated-LLM funnel).

Without these, the ecosystem is a single-tenant trust chain that
works only inside the ACC team.  Stage 2 is what lets external
publishers participate.

## Scope

Six sub-slices that can ship independently (operator picks ordering
based on which gates are most painful):

| Sub-slice | What ships | Operator gates |
|---|---|---|
| 2.1 | `flg77/acc-ecosystem` public repo bootstrap (Apache 2.0 + CLA + Konflux + spearhead→mirror promote) | `gh repo create` |
| 2.2 | Family extractions: `@acc/workspace-roles` (coding_agent family), `@acc/research-roles`, `@acc/business-roles`, `@acc/devops-roles`.  Two-release deprecation cycle ⇒ in-tree dirs removed in N+1 | Decide family boundaries via review of the existing tier classification |
| 2.3 | Public hub MVP at `acc-roles.dev` (static read-only — GitHub Pages or S3); publishing uses the existing acc1 internal hub until 2.5 | Decide hosting authority (single canonical vs federated per brainstorm Q2) |
| 2.4 | `acc/tui/screens/marketplace.py` — pkg-aware sibling to the existing Ecosystem pane.  `acc/tui/screens/catalogs.py` — Catalog admin (add/remove/priority).  WebGUI parity in `acc/webgui/routes_roles.py` | None — pure UI work, ships any time |
| 2.5 | `flg77/acc-podman-desktop` repo (public, Apache 2.0) — Podman Desktop plugin with curated-LLM picker as the first-run wizard (brainstorm Q7 consumer-edge funnel) | `gh repo create` + Podman Desktop extension marketplace registration |
| 2.6 | `docs/CONTRIBUTING-ROLE.md` + `docs/MIGRATING-FROM-INTREE.md` | None — pure docs |

### What's NOT in scope (Stage 3 territory)

* Bootc bundler / edge image / Hummingbird base — proposed in
  `openspec/changes/20260607-acc-pkg-edge-bootc/`.
* Federation (Phase F) — cross-hub A2A discovery; the substrate
  already exists in `acc/a2a/` from Stage 0 but the wire-up is
  post-Stage 2.

## Per-sub-slice file inventory

### 2.1 — `flg77/acc-ecosystem` bootstrap

| Where | What |
|---|---|
| New private repo `flg77/acc-ecosystem-spearhead` | dev branch (matches existing `acc-spearhead` discipline) |
| New public repo `flg77/acc-ecosystem` | mirror promoted via existing `acc-promote` tool |
| `LICENSE` (Apache 2.0), `CONTRIBUTING.md`, CLA hook | repo bootstrap |
| `.github/workflows/build.yml` | Konflux Pipeline reference + sigstore signing (reuses `gitops/tekton/pipelines/accpkg-build.yaml` from #27) |
| `packages/` directory tree | empty until 2.2 extractions land |
| `flg77/acc-ecosystem` GitHub Pages config | hosts `acc-roles.dev` (2.3) |

### 2.2 — Family extractions

| Family | In-tree source → package |
|---|---|
| `@acc/workspace-roles@1.0.0` | `roles/coding_agent*` (6 dirs) + bundled skills/MCPs per `tools/skill_mcp_tiers.yaml` |
| `@acc/research-roles@1.0.0` | `roles/research_*` (6 dirs) |
| `@acc/business-roles@1.0.0` | 30 business roles (HR, sales, marketing, ops, support, finance, legal, IT) |
| `@acc/devops-roles@1.0.0` | `data_engineer`, `devops_engineer`, `ml_engineer`, `security_analyst` |

**Migration mechanics** (per the ecosystem-implementation doc):

* Stage 2 release N — ship all four packages; in-tree dirs still
  present + `DeprecationWarning` emitted on load.
* Stage 2 release N+1 — delete in-tree dirs.  Git history is the
  long-term safety net (`git checkout v<N>.<x>.<y> -- roles/<name>/`).

### 2.3 — Public hub MVP

| Component | Decision needed |
|---|---|
| Static index at `https://acc-roles.dev/index.json` | GitHub Pages (free, ACC org-owned) vs S3 (cost + control) vs CloudFront-fronted S3 (cost + caching) |
| Blob storage | Public S3 bucket (read-only) OR GitHub Releases artefacts |
| Publish path | Stage 1.3's `acc-pkg publish` already POSTs to `<catalog>/upload/…`; hub needs an authenticated upload endpoint |
| Mirror discovery (Phase F seed) | A2A AgentCard already published per package; cross-hub fetch comes later |

### 2.4 — Discovery surfaces (TUI + WebGUI)

| File | Status |
|---|---|
| `acc/tui/screens/marketplace.py` (NEW) | Pkg-aware sibling to `ecosystem.py`; shows catalog availability + tier badge + signer; one-tap install via Compliance pane queue |
| `acc/tui/screens/catalogs.py` (NEW) | Catalog admin — list / add / remove / re-prioritise; renders to `<workspace>/.acc/catalogs.yaml` |
| `acc/tui/screens/ecosystem.py` (MODIFY) | List shows in-tree + installed-package roles together; tier badge column added |
| `acc/webgui/routes_roles.py` (NEW) | REST API parity: GET `/api/roles/available`, POST `/api/roles/install`, GET `/api/catalogs/`, POST `/api/catalogs/` |
| `acc/webgui/react/...` (NEW) | React surface for Marketplace + Catalog admin |

### 2.5 — `flg77/acc-podman-desktop`

| Component | Description |
|---|---|
| New repo `flg77/acc-podman-desktop` | TypeScript + Podman Desktop extension API |
| `src/extension.ts` — first-run wizard | Curated-LLM picker (RHOAI default panel from `ACC_RHOAI_PANEL_PATH`); "bring your own model" one-click |
| Shells out to `acc-pkg` binary | No parallel logic — Podman Desktop extension is glue + UI |
| Marketplace registration | Podman Desktop extension marketplace (Red Hat-operated) |

### 2.6 — Docs

| File | Purpose |
|---|---|
| `docs/CONTRIBUTING-ROLE.md` | First-time contributor walkthrough: `acc-pkg init` → write evals → cosign sign → publish in under an hour |
| `docs/MIGRATING-FROM-INTREE.md` | Two-release deprecation walk-through for operators tracking Stage 2 release N → N+1 |

## Impact

* **Affected code (Sub-slice 2.4 + 2.6 only — the parts shippable from `acc`):**
  * NEW `acc/tui/screens/marketplace.py`, `acc/tui/screens/catalogs.py`, `acc/webgui/routes_roles.py`
  * MODIFY `acc/tui/screens/ecosystem.py` (column + filter)
  * NEW `docs/CONTRIBUTING-ROLE.md`, `docs/MIGRATING-FROM-INTREE.md`
* **Sub-slices 2.1, 2.2, 2.3, 2.5 require operator-side actions** (`gh repo create`, GitHub Pages config, Podman Desktop marketplace registration); the proposal documents what changes when each completes.
* **No new env knobs in `acc/`** (Stage 2 lives mostly outside this repo).
* **Tests:**
  * 2.4 — ~50 tests across Marketplace + Catalog admin (Textual app harness + WebGUI route)
  * 2.6 — none (docs)
  * Other sub-slices have their own test suites in their target repos

## Open strategic decisions

1. **Hub hosting authority** — single canonical hub at
   `acc-roles.dev` (Anthropic / Red Hat / foundation-operated) or
   federated from day 1?  Recommendation per brainstorm Q2: single
   canonical for Phase 2; federation in Phase F.
2. **Verified-Publisher pricing** — free for OSS-only authors;
   $5-50k/year for commercial vendors.  Recommendation per Q3:
   free-for-OSS.
3. **Family boundaries** — review `tools/skill_mcp_tiers.yaml`
   classifications + decide whether `@acc/devops-roles` is its own
   family or rolls into `@acc/business-roles`.
4. **CLA mechanism** — DCO sign-off (lightweight) or full CLA
   (CLA Assistant or EasyCLA)?  Apache 2.0 + DCO covers most cases;
   full CLA gives the relicense-to-premium-pack option per the
   v0.3.53 strategy.

## What stays open after Stage 2

* **Stage 3** — bootc bundler for edge deployment
  (`openspec/changes/20260607-acc-pkg-edge-bootc/`).
* **Phase F** — cross-hub A2A discovery, private corporate hubs.
* **Tidelift-style maintainer payments** (Stream 6 of the v0.3.53
  business model) — funded when Marketplace fees (Stream 1) +
  Verified Publisher subscriptions (Stream 2) cover platform costs.
* **Compliance pane Marketplace integration** — the Package
  Proposals tab (PR #32) is the install surface; once Stage 2 ships
  the public hub, the panel can show tier + Verified Publisher
  badges sourced from `acc-roles.dev`'s authority.

## References

* Stage 0 pilot: `openspec/changes/20260603-acc-pkg-pilot/proposal.md`
* Stage 1 proposal: `openspec/changes/20260605-acc-pkg-trust-and-assistant/proposal.md`
* Architecture: `openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md`
* Brainstorm Q7 + Q8: `<vault>/ACC Openspec/ACC Role Ecosystem/Ecosystem split — brainstorm.md`
* Naming convention: `openspec/RENAMES.md` (functional, no
  `-role-proposal-` infix).
