# Ecosystem implementation — reference

> **Doc lifecycle.** This file lives here, in the
> `20260604-role-ecosystem-strategy/` folder, while the four stage
> sub-proposals (Stage 0 already filed; Stages 1–3 deferred) are
> in-flight. It is the **canonical architecture reference** that
> each stage sub-proposal cites. After Stage 3 ships, this content
> + a sibling `architecture.md` get promoted to the repo's
> top-level `docs/` folder as user-facing documentation.

> **What this is not.** This is not a proposal — it ships no code,
> needs no Phase 1 / Impact section, and is not gated by a separate
> ratification. It belongs to the strategy proposal as a companion
> reference, the way `strategic-decisions.md` and
> `competitive-analysis.md` belong (per the v0.3.53 strategy
> pattern).

## Why a single architecture reference

The ACC Role Ecosystem brainstorm
(`<vault>\ACC Openspec\ACC Role Ecosystem\Ecosystem split — brainstorm.md`)
answered the eight tactical decisions + Q3a (skills/MCPs tier
policy) + Q3b (catalog file + signing floor). The strategy
proposal sequences Phase A–F. The package format proposal
(`20260531-acc-role-package-format`) pins the `.accpkg` shape.

What was still missing: a **single document** describing *how every
piece composes* — components, files to touch, deprecations, the
edge↔DC seam. Without it, each stage sub-proposal would
re-derive the same wiring from scratch.

This doc serves three audiences:

* **Contributors** writing the stage sub-proposals — what to touch,
  what to add, what to deprecate.
* **Operators** deploying ACC at edge or DC — how the same
  `acc-pkg` CLI + catalog substrate scales from a Podman Desktop
  laptop to a RHOAI-flanked OpenShift cluster.
* **The Assistant** itself (when self-improvement loops eventually
  read OpenSpec) — single source of truth for "what counts as
  the ecosystem."

## Stage sub-proposals — what ships where

| Stage | Sub-proposal | What ships | Status |
|---|---|---|---|
| 0 | `20260603-acc-pkg-pilot` | `acc-pkg` minimal CLI + catalog + cosign verify + 1-role pilot | Filed |
| 1 | (deferred) `20260605-acc-pkg-trust-and-assistant` | Enterprise Contract depth, OIDC keyless, evals, `PROPOSE_INFUSE` runtime | Not yet |
| 2 | (deferred) `20260606-acc-ecosystem-hub-and-scale` | Public hub MVP, family extractions, CLA, CONTRIBUTING-ROLE.md | Not yet |
| 3 | (deferred) `20260607-acc-pkg-edge-bootc` | Bootc bundler, Hummingbird base, RHEL bootc + MicroShift opt-in | Not yet |

## Architecture overview

### Two repos, four staging surfaces, one seam

```
┌────────────────────────────────────────────────────────────────────┐
│                      THE SINGLE SEAM                                │
│                                                                     │
│   acc-pkg CLI  +  /etc/acc/catalogs.yaml  +  .accpkg format        │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
            │                              │
            │                              │
    ┌───────▼───────┐              ┌───────▼────────┐
    │   acc repo    │              │ acc-ecosystem  │
    │  (this repo)  │              │     repo       │
    │               │              │   (Stage 2)    │
    │  acc/pkg/     │              │                │
    │  acc-pkg CLI  │              │ packages/      │
    │  catalog code │   pulls      │   @acc/        │
    │  bootc bundler│ ◄──────────► │   workspace/   │
    │  core 7 roles │              │   research/    │
    │  12 stdlib    │              │   business/    │
    │  skills + 3   │              │   devops/      │
    │  MCPs (baseline)             │                │
    └───────┬───────┘              └────────────────┘
            │                              ▲
            │                              │
            │                              │ publish (cosign, OIDC)
            │                              │
   ┌────────┴─────────┬───────────────────────────┐
   │                  │                           │
   ▼                  ▼                           ▼
EDGE             CONSUMER EDGE            DATA CENTER
(bootc image)    (Podman Desktop /        (OpenShift +
                  ACC TUI / WebUI)         acc-operator)
                                              │
   File catalog     File catalog              ▼
   (offline)        + curated-LLM         HTTPS catalog
                    funnel                (RHTAP-signed)
                                          │
                                          ▼
                                    Side-by-side
                                    with RHOAI +
                                    Developer Hub +
                                    Trusted Artifact
                                    Signer
```

### The seam in detail

Three things constitute the seam — they are the *only* contract
between repos / surfaces:

1. **`.accpkg` format v1** — manifest (Pydantic + JSON Schema),
   `roles/`, `skills/`, `mcps/`, `memory-seed/`, `golden-prompts/`,
   `evals/`, `policy/`, `signatures/`. Defined in
   `20260531-acc-role-package-format`, pinned by Stage 0.
2. **Catalog file schema** (brainstorm Q3b) — layered loader,
   `mode: https | file`, `required_signer:` per tier. Defined
   structurally here; implemented in Stage 0.
3. **`acc-pkg` CLI** — universal automation seam (build / install /
   verify / bundle / inspect / list / publish / login). Stage 0
   ships the first four; Stage 1 adds publish/login; Stage 3 adds
   bundle.

Anything that crosses repo or surface boundaries goes through the
seam. No alternative path.

## Components — added

### Core (`acc/` repo)

| Component | Path | Stage | What it does |
|---|---|---|---|
| `acc/pkg/manifest.py` | NEW | 0 | Pydantic `AccPkgManifest` v1 + JSON Schema |
| `acc/pkg/build.py` | NEW | 0 | Deterministic tarball + sha256 |
| `acc/pkg/install.py` | NEW | 0 | sha256 + topo-sort + unpack + registry update |
| `acc/pkg/verify.py` | NEW | 0 (sig floor) / 1 (EC depth) | Cosign verify against catalog signer; EC policy depth |
| `acc/pkg/catalog.py` | NEW | 0 | Layered catalog loader + resolver |
| `acc/pkg/registry.py` | NEW | 0 | flock-protected JSON index at `/var/lib/acc/packages/registry.json` |
| `acc/pkg/cli.py` | NEW | 0 | `python -m acc.pkg` + `acc-pkg` console script |
| `acc/pkg/bundle.py` | NEW | 3 | Bootc bundler — `--base {hummingbird\|rhel-bootc\|microshift\|<custom>}` |
| `acc/pkg/evals.py` | NEW | 1 | Behavioral + safety eval runner (extends `acc-bench`) |
| `acc/pkg/publish.py` | NEW | 1 | OIDC keyless cosign sign + push to catalog hub |
| Default `/etc/acc/catalogs.yaml.example` | NEW | 0 | Operator-installed catalog sample |
| `policy/enterprise-contract.yaml` | NEW | 1 | Default EC policy bundled with installer |

### Runtime extensions

| Component | Path | Stage | What it does |
|---|---|---|---|
| `PROPOSE_INFUSE` marker | extends `acc/assistant_proposal.py` | 1 | Family member alongside existing PROPOSE_* markers (per v0.3.47 form-tolerance) |
| Marketplace TUI pane | `acc/tui/screens/marketplace.py` (NEW) | 2 | Sibling to existing `ecosystem.py` / `infuse.py`; lists available packages from catalogs, install dialog |
| Catalog admin TUI | `acc/tui/screens/catalogs.py` (NEW) | 2 | Add/remove catalogs, view tier+signer, set priority |
| WebGUI `/roles` route | `acc/webgui/routes_roles.py` (NEW) | 2 | Web parity for Marketplace + Catalog admin |
| `acc-deploy.sh` package fetch | MODIFY | 2 | At startup, resolve declared packages from `collective.yaml` → fetch + verify + install before agent boot |
| Podman Desktop plugin | NEW repo `flg77/acc-podman-desktop` | 2 | Consumer-edge funnel (brainstorm Q7); first-run wizard surfaces curated-LLM panel |

### Trust + identity

| Component | Path | Stage | What it does |
|---|---|---|---|
| Konflux pipeline templates | `.tekton/` in `acc-ecosystem` repo | 1 | Build + sign + Rekor-attest each `.accpkg` |
| Enterprise Contract policy | `policy/enterprise-contract.yaml` | 1 | Default required attestations: build provenance + eval pass + Cat-A/B/C smoke |
| Default catalog list | shipped sample | 0 | `acc-canonical` (trusted) + `community-public` (community) |
| `tools/cosign-pilot-keygen.sh` | NEW | 0 | Operator helper: generate local keypair for pilot before Stage 1 swaps in OIDC keyless |

### Repos — three independent siblings + internal dev hub

All three ecosystem-adjacent repos are **independent siblings** of
`acc`. None nest inside another. They share the `.accpkg` format +
catalog seam, nothing else.

| Repo | Visibility | Stage | Role |
|---|---|---|---|
| `flg77/acc-ecosystem` | Public, Apache 2.0 + CLA | 2 | Public package source; mirrors from a private spearhead via the same `acc-promote` discipline `acc` uses |
| `flg77/acc-web` | Public, Apache 2.0 | shipped | Project website (`acc-web-project` in prior naming); ecosystem touches it only via the Roles / Marketplace surfaces in Stage 2 |
| `flg77/acc-podman-desktop` | Public, Apache 2.0 | 2 | Consumer-edge funnel; shells out to `acc-pkg` binary — no parallel logic |
| Internal hub on `acc1` Kubernetes | Private, ACC-team-only | **0** | First catalog endpoint for Stage 0 + Stage 1; serves built `.accpkg` blobs + `index.json` + cosign signatures over HTTPS; replaced by a public hub at Stage 2 |
| Public hub: `acc-roles.dev` | Public read-only (Stage 2), publish OIDC (Stage 3) | 2 | Promoted from the internal `acc1` endpoint; static GitHub Pages or S3 — Stage 2 decision |

### Where roles live during Stages 0 + 1

**Roles stay in this (`acc`) repo for Stages 0 and 1.** The extraction
to a separate `acc-ecosystem` repo is a **Stage 2** action, not
Stage 0. Stage 0's pilot builds `.accpkg`s **in place** from
`acc/roles/<name>/` directories, publishes them to the internal
`acc1` Kubernetes hub, and verifies round-trip install into a
vanilla container. No repo split happens until the format and the
trust chain (Stage 1) have proven themselves on the internal hub.

This is a deliberate cut: it avoids the dual-repo coordination
overhead during the first two stages, and the internal `acc1`
hub gives the team a fast development feedback loop without
exposing half-baked packages to a public surface.

### Ownership and identity (for now)

All packages are **ACC-owned** during Stages 0–2. There is **no
Red Hat Foundation relationship** at this point — the eventual
RHEL / RHOAI / RHTAP alignment is the *trajectory*, not the
current state. Concretely:
* Signing identity: ACC's own OIDC issuer (or local cosign keypair
  in Stage 0; see `tools/cosign-pilot-keygen.sh`).
* Catalog `required_signer:` patterns point at ACC's identity,
  not Red Hat's.
* Trust-chain wiring (Konflux pipeline templates, Enterprise
  Contract policy) targets RHTAP-shape *compatibility* so the
  future migration is straightforward, but the runtime today is
  ACC's own infrastructure.
* The internal `acc1` Kubernetes hub is operated by the ACC team;
  no foundation governance applies.

When the relationship lands, what changes is the catalog
`required_signer:` entry for the canonical trusted tier and the
Konflux pipeline's signing identity — the rest of the architecture
is unchanged.

### Operator (DC)

| Component | Path | Stage | What it does |
|---|---|---|---|
| `AccPackageInstall` CR | `operator/api/v1alpha1/accpackageinstall_types.go` (NEW) | 1 | DC-side declarative install of `@scope/name@version` against named catalog |
| `AccCatalog` CR | `operator/api/v1alpha1/acccatalog_types.go` (NEW) | 1 | Declares cluster-wide catalogs; renders to per-pod `/etc/acc/catalogs.yaml` ConfigMap |
| `AccPackageInstall` controller | `operator/internal/controller/accpackageinstall_controller.go` (NEW) | 1 | Reconciles desired state by invoking `acc-pkg install` inside the ACC pod |
| OLM bundle update | `operator/bundle/` | 1 | New CRDs added to ClusterServiceVersion |
| RHTAP integration | `operator/Makefile` Konflux target | 1 | Bundle goes through RH Trusted Application Pipeline alongside RHOAI |
| GitOps templates | `gitops/argocd/applications/accpackage-*.yaml` (NEW) | 1 | Manual+Execution path via ArgoCD; AccPackageInstall objects under GitOps |

## Components — modified

### `acc/cognitive_core.py`
* **Today:** loads role.yaml from `roles/<name>/role.yaml` only.
* **Stage 1 change:** dual-source loader — first checks
  `/var/lib/acc/packages/<scope>/<name>-<version>/roles/<name>/role.yaml`
  (installed package), falls back to `roles/<name>/role.yaml` (in-tree
  baseline). Resolution path logged for audit.
* **Stage 2 change:** in-tree fallback restricted to the 7 CONTROL
  roles; movable roles must come from a package.

### `acc/assistant_proposal.py`
* Add `PROPOSE_INFUSE` marker handling per the v0.3.47 marker-family
  pattern (sibling to `PROPOSE_PLAN` / `PROPOSE_EXECUTE` / etc.).
* New routing path: `PROPOSE_INFUSE` → AoA-P2b queue → Compliance
  pane with tier badge + signer + alternates → on approval, invoke
  `acc-pkg install` via subprocess.

### `acc/capability_index.py`
* Subscribe to `acc-pkg install`/`uninstall` events; trigger
  CapabilityIndex SIGHUP (already exists in v0.3.42 — extending the
  trigger sources).

### `acc/collective.py`
* New optional field `CollectiveSpec.required_packages: list[str]` —
  semver-pinned package list resolved at collective boot before any
  agent spawn.
* Per-collective catalog override: `<workspace>/.acc/catalogs.yaml`
  loaded if present.

### `acc/governance.py` + `acc/governance_capabilities.py`
* Package-vetted Cat-A/B/C bounds (from `policy/policy-bounds.yaml`
  inside an `.accpkg`) become *defaults*. Operator override
  (existing `policy/overlay.yaml`) still source of truth. SIP-P2
  rails still gate.

### `acc/tui/screens/ecosystem.py`
* Today: lists in-tree roles for one-tap infuse.
* Stage 2 change: shows in-tree + installed-package roles in one
  list, distinguished by tier badge. The Marketplace pane is the
  new pkg-aware sibling; the Ecosystem pane stays for in-tree
  baseline.

### `acc/tui/screens/compliance.py`
* Add new tab: **Package proposals**. Shows queued
  `PROPOSE_INFUSE`s with tier badge, signer identity, alternates,
  attestation summary. Approve / reject / select-alternate-catalog.

### `acc/webgui/routes_governance.py`
* WebGUI parity for the Package-proposals tab.

### `acc-deploy.sh`
* Stage 2: parse `collective.yaml` for `required_packages:`, fetch
  + verify + install before agent boot.
* Stage 3: when invoked with `--mode bundle`, delegate to
  `acc-pkg bundle` for bootc image production.

### `operator/`
* Stage 1: add CRDs, controllers, RBAC for `AccPackageInstall` +
  `AccCatalog`. Existing operator stays functional unchanged.

### `gitops/`
* Stage 1: ArgoCD Application templates that wrap
  `AccPackageInstall` resources; declarative DC install path.

### Tests / docs / fixtures
* Stage 2: any hard-coded role list (TUI dropdown fixtures, sample
  `collective.yaml` files in `samples/`, ACC docs site) updated to
  reference package source — both in-tree (Phase 1) and via test
  catalog.

## Components — deprecated

### Phase A (Stage 0/1) — softly deprecated, kept working

* **None.** Backward-compat is strict: in-tree movable roles still
  load, in-tree skills/MCPs still resolve, existing collectives
  still boot.

### Phase B (Stage 2) — hard deprecation

* **`roles/<movable-name>/`** — 44 directories deleted from `acc`
  tree. CONTROL roles (7) stay.
* **`skills/<bundled-into-pack>/`** — non-baseline skills extracted
  to their packs; the 12 stdlib skills stay in `acc/`.
* **`mcps/<bundled-into-pack>/`** — non-baseline MCPs extracted; the
  universal triad (arxiv / wikipedia / semantic_scholar) stays.
* **Hard-coded role-name lists** in test fixtures
  (`tests/conftest.py` movable-role fixtures), TUI dropdown
  defaults, `operator/Makefile` catalog-build target.
* **`acc-deploy.sh up` single-image-with-everything path** — image
  shrinks to core + baseline; `collective.yaml` drives package
  fetch.

### Phase C (Stage 3) — edge-only

* **`acc/cluster.py` "build standalone container image" recipe** —
  superseded by bootc bundler for the edge case. Standalone Podman
  Compose path stays for laptop dev.

## File-level change inventory

The big table — every file that changes across all four stages.
This is the contributor-facing index that the stage sub-proposals
defer to.

### Stage 0 — `acc-pkg` pilot (`20260603-acc-pkg-pilot`)

| Path | Change | Notes |
|---|---|---|
| `acc/pkg/__init__.py` | NEW | Module marker |
| `acc/pkg/manifest.py` | NEW | Pydantic `AccPkgManifest` v1 |
| `acc/pkg/build.py` | NEW | Deterministic tarball + sha256 |
| `acc/pkg/install.py` | NEW | Topo-sort + unpack + registry |
| `acc/pkg/verify.py` | NEW | cosign verify against catalog signer |
| `acc/pkg/catalog.py` | NEW | Layered loader + resolver |
| `acc/pkg/registry.py` | NEW | flock JSON index |
| `acc/pkg/cli.py` | NEW | argparse + console script |
| `acc/pkg/schema/accpkg-v1.json` | NEW | JSON Schema export |
| `pyproject.toml` | MODIFY | `acc-pkg` console script entry |
| `tools/classify_skills_mcps.py` | NEW | Tier-classification script |
| `tools/skill_mcp_tiers.yaml` | NEW | Committed classification output |
| `tools/extract_role.py` | NEW | Single-role mover |
| `tools/cosign-pilot-keygen.sh` | NEW | Pilot keypair helper |
| `examples/catalogs.yaml` | NEW | Operator-installed sample |
| `tests/pkg/*` (~40 tests) | NEW | Schema, build/install, registry, catalog, sign |
| `tests/pkg/fixtures/vanilla_acc.dockerfile` | NEW | Test container with coding_agent removed |
| `tests/pkg/test_pilot_roundtrip.py` | NEW | Extract → build → install → golden-prompt pass |
| `docs/acc-pkg.md` | NEW | Operator-facing CLI reference (this stage may ship a stub; full docs land post-Stage 3) |

### Stage 1 — trust chain + Assistant runtime (deferred sub-proposal)

| Path | Change | Notes |
|---|---|---|
| `acc/pkg/verify.py` | MODIFY | Add Enterprise Contract policy depth |
| `acc/pkg/publish.py` | NEW | OIDC keyless cosign + Rekor + Fulcio |
| `acc/pkg/evals.py` | NEW | Behavioral + safety eval runner |
| `policy/enterprise-contract.yaml` | NEW | Default policy bundled with installer |
| `acc/assistant_proposal.py` | MODIFY | PROPOSE_INFUSE marker family member |
| `acc/cognitive_core.py` | MODIFY | Dual-source role loader (package → in-tree fallback) |
| `acc/collective.py` | MODIFY | `required_packages:` + per-collective catalog override |
| `acc/tui/screens/compliance.py` | MODIFY | Package proposals tab |
| `acc/webgui/routes_governance.py` | MODIFY | WebGUI parity |
| `acc-bench` skill | MODIFY | Per-package eval-history JSONL |
| `operator/api/v1alpha1/accpackageinstall_types.go` | NEW | CR |
| `operator/api/v1alpha1/acccatalog_types.go` | NEW | CR |
| `operator/internal/controller/accpackageinstall_controller.go` | NEW | Reconciler |
| `operator/internal/controller/acccatalog_controller.go` | NEW | Reconciler |
| `operator/bundle/manifests/` | MODIFY | CSV + CRD manifests |
| `operator/config/rbac/role.yaml` | MODIFY | RBAC for new CRs |
| `gitops/argocd/applications/accpackage-sample.yaml` | NEW | Sample GitOps |
| `gitops/tekton/pipelines/accpkg-build.yaml` | NEW | Konflux pipeline template |
| `tests/operator/` | MODIFY | New CR controller tests |

### Stage 2 — public hub + family extractions (deferred sub-proposal)

| Path | Change | Notes |
|---|---|---|
| `roles/coding_agent*` (6 dirs) | REMOVE | Extracted to `@acc/workspace-roles` |
| `roles/research_*` (6 dirs) | REMOVE | Extracted to `@acc/research-roles` |
| `roles/<business-30>` | REMOVE | Extracted to `@acc/business-roles` |
| `skills/<bundled>` | REMOVE | Per tier classification |
| `mcps/<bundled>` | REMOVE | Per tier classification |
| `acc/tui/screens/marketplace.py` | NEW | Package-aware sibling to ecosystem.py |
| `acc/tui/screens/catalogs.py` | NEW | Catalog admin |
| `acc/webgui/routes_roles.py` | NEW | WebGUI parity |
| `acc-deploy.sh` | MODIFY | required_packages fetch at boot |
| `flg77/acc-ecosystem` repo | NEW | Public mirror, Apache 2.0 + CLA |
| `acc-roles.dev` hub | NEW | Static index + blob storage |
| `tests/conftest.py` | MODIFY | Hard-coded role-list fixtures → catalog-driven |
| `operator/Makefile` | MODIFY | catalog-build target deprecated |
| `samples/collective-*.yaml` | MODIFY | Reference `required_packages:` |

### Stage 3 — edge bootc (deferred sub-proposal)

| Path | Change | Notes |
|---|---|---|
| `acc/pkg/bundle.py` | NEW | Bootc bundler with `--base` selector |
| `acc/pkg/cli.py` | MODIFY | `bundle` subcommand |
| `gitops/bootc/Containerfile.bootc.j2` | NEW | Jinja template for layered image |
| `gitops/bootc/hummingbird.yaml` | NEW | Hummingbird base config |
| `gitops/bootc/rhel-bootc.yaml` | NEW | RHEL bootc base config |
| `gitops/bootc/microshift.yaml` | NEW | MicroShift base config |
| `flg77/acc-podman-desktop` repo | NEW | Consumer-edge funnel |
| `tests/pkg/test_bundle.py` | NEW | Bundler tests with mocked podman |

### Post-Stage-3 — final user-facing documentation (separate docs PR)

After Stage 3 ships, this file's content + a sibling
`architecture.md` get promoted to the repo's `docs/` folder as
final user-facing documentation:

| Path | Change | Notes |
|---|---|---|
| `docs/ecosystem-implementation.md` | NEW | Promoted from this file; operator-voice render |
| `docs/architecture.md` | NEW | Sibling architecture reference; component diagrams + the seam contract |
| `docs/acc-pkg.md` | EXPAND | Full CLI reference superseding the Stage-0 stub |
| `docs/CONTRIBUTING-ROLE.md` | EXPAND if landed in Stage 2 | First-time contributor walkthrough; OIDC + cosign workflow |
| `docs/MIGRATING-FROM-INTREE.md` | NEW | Two-release deprecation walk-through for operators tracking Stage 2 → Stage 2 N+1 |

## Edge ↔ DC scaling — one architecture, two substrates

Per the brainstorm Q3 infusion matrix and Q5 edge deployment, the
same `acc-pkg` + catalog seam scales across three deployment
shapes.

### Single-node Consumer Edge (laptop / homelab)

* **Substrate:** Podman Desktop (or plain Podman) on the host OS.
* **ACC core:** runs in a single Podman Compose project.
* **Catalogs:** `~/.acc/catalogs.yaml` declares `acc-canonical`
  (trusted https) + optional `community-public` (community https) +
  local file-mode catalog at `~/acc-packages/` for offline / dev.
* **Funnel:** ACC-Podman-Desktop plugin (Stage 2) is the first-run
  surface; recommends RHOAI-curated LLM panel via the curated-LLM
  picker; "bring your own model" is one click further (brainstorm
  Q7).
* **Identity:** keyless cosign via GitHub Actions OIDC for any
  packages the user publishes from this machine.
* **Update model:** `acc-pkg install @scope/name@new-version` per
  package; idempotent re-install on existing version.

### Edge (single-node, disconnected / hostile network)

* **Substrate:** bootc image. Default base = Fedora Hummingbird
  (`quay.io/hummingbird-community/bootc-os`). Fallbacks: plain
  `rhel-bootc`, `microshift` (single-node OCP), or
  operator-supplied custom OCI bootc base.
* **ACC core + packages:** baked at build time into Layer 2 +
  Layer 3 of the bootc image (brainstorm Q5).
* **Catalogs:** `mode: file` catalog at `/var/lib/acc/packages` (or
  whatever path the bundler chose); zero hub access required.
* **Identity:** packages signed at build time by the bundler's
  identity (operator's cosign keyref or OIDC); verified at install
  time against the file catalog's `required_signer:`.
* **Update model:** `bootc switch` to a new image tag;
  transactional rollback on failed boot. Individual package
  updates are NOT supported on edge (rebuild + redeploy is the
  unit).
* **Connectivity:** NATS bridge (ACC-9 shipped) carries verdicts
  back to a hub when connectivity returns; never blocks edge work.

### Data Center (OpenShift + RHOAI + RHTAP)

* **Substrate:** OpenShift cluster running the existing
  `acc-operator` (Go-based OLM operator).
* **ACC core:** deployed via the operator's `AccCluster` CR.
* **Catalogs:** declarative — `AccCatalog` CRs (Stage 1) render to
  per-pod `/etc/acc/catalogs.yaml` ConfigMaps. Typical mix:
  `acc-canonical` (trusted https) + `redhat-tp` (Verified Publisher
  https) + `corp-internal` (private S3 / Quay file mode for
  air-gap).
* **Identity:** Red Hat Trusted Artifact Signer instance (separate
  operator within Trusted Application Pipeline; brainstorm Q4
  shipping topology). Side-by-side with RHOAI on the same cluster.
* **Update model:** `AccPackageInstall` CRs reconciled by the
  controller; ArgoCD Application objects wrap them for GitOps;
  RHTAP build pipelines publish updates as new versions; operator
  upgrades by editing the CR.
* **Eval surface:** RHOAI ships the curated LLM panel; the
  controller can invoke per-package evals against RHOAI's served
  models at install time.
* **Federation (Phase F):** A2A AgentCard discovery
  (`acc/a2a/card.py` shipped) carries package metadata across
  hubs; private corporate hubs interop with public canonical.

### The constants across all three

| | Single-node | Edge bootc | DC OpenShift |
|---|---|---|---|
| `.accpkg` format | same | same | same |
| `acc-pkg` CLI | same binary | same binary | same binary (inside pod) |
| Catalog schema | same | same | same |
| Signing floor | same (cosign-required) | same | same |
| Tier matrix | same (trusted/tp/community/self) | same | same |
| Manifest schema | same | same | same |
| Eval runner | same (acc-bench) | same | same (against RHOAI panel) |

What differs is **deployment substrate + catalog mode + identity
plane** — none of which change the seam. The Assistant infuse
workflow (brainstorm Q3b) works identically across all three; the
operator just sees a different tier badge + catalog source.

## Phase ordering

```
Stage 0
   20260603-acc-pkg-pilot (filed)
   - Real cosign verify (signing floor)
   - Catalog substrate (https + file)
   - 1-role pilot extraction (coding_agent)
   - acc-ecosystem-spearhead private repo bootstrap
   ↓
Stage 1 — trust chain + Assistant runtime (Q3 2026)
   - PROPOSE_INFUSE marker family
   - Dual-source role loader in cognitive_core
   - Enterprise Contract policy depth
   - OIDC keyless cosign + Fulcio + Rekor
   - Behavioral + safety eval YAML format
   - operator/ AccPackageInstall + AccCatalog CRs
   - Konflux pipeline templates
   ↓
Stage 2 — public hub + family extractions (Q4 2026)
   - acc-ecosystem public mirror + Apache 2.0 + CLA
   - acc-roles.dev hub MVP
   - Marketplace TUI + WebGUI surfaces
   - Family extractions (coding_agent variants, research_*, business)
   - 44 in-tree role dirs removed; tests/docs updated
   - acc-podman-desktop plugin
   ↓
Stage 3 — edge bootc (Q1 2027)
   - Bootc bundler with --base selector
   - Hummingbird as canonical agentic-edge OS
   - RHEL bootc / MicroShift / custom fallback bases
   ↓
Post-Stage-3 — final docs PR
   - Promote this file → docs/ecosystem-implementation.md
   - Add docs/architecture.md sibling
   - Expand docs/acc-pkg.md to full CLI reference
   ↓
Stage F — federation (later)
   - A2A cross-hub discovery (substrate shipped; Stage F wires it)
   - Private corporate hubs
```

## Role update model

A separate concern from the in-tree → package migration: once roles
ship as packages, **how do role updates propagate?**

### Versions are immutable; updates are new versions

`@acc/coding-roles@1.2.0` is forever 1.2.0 — same tarball, same
sha256, same cosign signature, same Rekor entry. A new release
cuts `@acc/coding-roles@1.3.0`. The old version stays available
for anyone still pinned to it.

### Multiple versions coexist on disk

```
/var/lib/acc/packages/
├── @acc/
│   ├── coding-roles-1.2.0/      ← currently active in some collective
│   ├── coding-roles-1.3.0/      ← newer; installed but not active yet
│   └── research-roles-2.1.0/
└── registry.json                 ← who references what
```

Per-collective pinning resolves which one runs:

```yaml
# workspace/collective.yaml
required_packages:
  - "@acc/coding-roles@1.2.0"     # locked exact
  - "@acc/research-roles@^2.0"    # semver range — resolver picks newest compatible
```

### Update workflow — four paths, one mechanism

| Path | How update flows |
|---|---|
| **Assistant + Execution** | Catalog index refresh surfaces new version → Assistant emits `[PROPOSE_INFUSE:@acc/coding-roles@1.3.0]` → Compliance pane shows "upgrade from 1.2.0" diff → operator approves → `acc-pkg install` puts 1.3.0 alongside 1.2.0 → arbiter re-issues `ROLE_ASSIGN` at the new version |
| **Manual + Execution (DC)** | Operator bumps `required_packages:` in `collective.yaml`, OR ArgoCD reconciles `AccPackageInstall.spec.version: 1.3.0` → controller installs → arbiter re-rolls |
| **Manual + Execution (consumer-edge)** | `acc-pkg install @acc/coding-roles@1.3.0` from CLI / cron / Ansible → idempotent, side-by-side install |
| **Manual + Prepackage (edge bootc)** | NOT a package update — rebuild + ship a new bootc image; `bootc switch` to the new tag; transactional rollback on failed boot |

### Rollback is free

Edit `collective.yaml` back to `@acc/coding-roles@1.2.0` → arbiter
re-rolls. The old version is still on disk until GC. Zero
re-download cost. This is the main reason side-by-side installs
matter.

### Cache GC

`acc-pkg gc` removes versions with **zero live references** in
`registry.json` — no `collective.yaml` pins them, no active
`ROLE_ASSIGN` mentions them. Configurable retention floor: keep
last N versions per package (default 3) regardless of references,
so a panicked rollback to an older known-good version doesn't
require a re-fetch.

### Skill / MCP version drift inside packages

* Skills/MCPs **bundled in** a role pack ride the pack's version —
  they update when the pack does.
* Skills/MCPs in their **own scoped pack** (e.g.
  `@acc/skills-pandas-toolkit@^1.4`) update independently; the role
  pack declares a semver range via `depends_on:` and the resolver
  picks the newest compatible version at install time.
* Both paths go through the **signed dep closure** (brainstorm
  Q3a defuse #5) so the role pack's attestation knows exactly
  which dep versions it was eval'd against. A skill pack update
  that violates the role pack's semver range is refused at install.

### Edge update model: whole-image, not in-place

The bootc edge case is intentionally NOT in-place: edge hosts get
image swaps via `bootc switch`, not `acc-pkg install`. The signed
image is the unit of trust; per-package updates on edge would
re-introduce the drift problem the image-mode model is meant to
solve. If a single role pack needs a hotfix on edge, the operator
rebuilds + reships the bootc image.

### Drift detection during life of a version

`acc-bench`'s per-package eval-history JSONL (Q7) tracks
behavioral drift at the installed-version level. A regression
triggers `RECOMMEND_UNINSTALL` on the Compliance pane — operator
can downgrade (free rollback as above) or pin to a still-good
older version.

## Migration plan

Backward compatibility is strict during Stages 0 + 1. During
Stage 2:

1. **One-minor-release parallel window.** Stage 2 release N ships
   with in-tree movable roles still present BUT emits a
   deprecation warning when loaded. The next minor release (N+1)
   removes them. Git history is the long-term safety net: operators
   needing a deleted in-tree copy after N+1 recover it via
   `git checkout v<N>.<x>.<y> -- roles/<name>/`.
2. **Test fixtures migrate first.** Stage 2's PR adjusts test
   fixtures to consume from a local file catalog (Stage 0's
   tooling) before any real removal.
3. **Operator migration guide.** `docs/MIGRATING-FROM-INTREE.md`
   walks operators: declare `required_packages:` in
   `collective.yaml`, restart, verify. ACC's `acc-deploy.sh up`
   no-ops on idempotent re-install.
4. **Sample collectives updated.** `samples/collective-*.yaml`
   gain `required_packages:` blocks.

## Risk register

| Risk | Mitigation | Owner stage |
|---|---|---|
| Signing-floor friction for community publishers | Keyless OIDC via GitHub Actions = afternoon setup (brainstorm Q8) | 1 |
| Catalog priority + per-tier policy depth confusion | Compliance pane shows tier badge + alternates explicitly; operator can pin catalog | 0 |
| In-tree → package extraction breaks tests | Two-release deprecation cycle; fixtures migrate first | 2 |
| Hummingbird experimental + may slip | `--base` is a per-bundle build flag (brainstorm Q5); rhel-bootc is the always-available fallback | 3 |
| RHOAI panel composition changes | Catalog schema declares `include_rhoai_default: true` (brainstorm Q7); customer additions extend | 1 |
| Public hub becomes infrastructure cost burden | Static read-only Phase 2 (S3 / GitHub Pages); publish-OIDC is Stage 1+ work; Stream 1+2 revenue covers it (v0.3.53 strategy) | 2 |
| `acc-podman-desktop` plugin diverges from ACC core CLI | Plugin shells out to `acc-pkg` binary; no parallel logic | 2 |
| DC `AccPackageInstall` controller conflicts with manual `acc-pkg install` | Controller is reconciler — adopts manually-installed packages on next reconcile; never destroys operator-state | 1 |

## Open strategic decisions

* **Public hub hosting for Stage 2** — GitHub Pages / S3 /
  CloudFront-fronted S3? (Internal `acc1` Kubernetes hub serves
  Stages 0 + 1; public hub decision deferred to Stage 2.)
* **Canonical Enterprise Contract policy ownership** — ACC /
  Red Hat / foundation?
* ~~Stage 2 in-tree-removal trigger~~ **Decided: one minor
  release.** Stage 2 release N ships with both in-tree movable
  roles and their package equivalents working in parallel +
  deprecation warning. The very next minor release (N+1) deletes
  the in-tree dirs. Rationale: roles are version-controlled in
  git, so any operator who needs to resurrect a deleted in-tree
  copy can branch from release N — that's a stronger durability
  guarantee than a longer in-repo parallel window, with no
  ongoing maintenance cost. Operators on the slow track who miss
  the migration window can still recover via `git checkout
  v<N>.<x>.<y> -- roles/<name>/`.

## References

* Brainstorm:
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC Openspec\ACC Role Ecosystem\Ecosystem split — brainstorm.md`
* Strategy (this folder's proposal): `proposal.md`
* Format: `openspec/changes/20260531-acc-role-package-format/proposal.md`
* Stage 0 pilot: `openspec/changes/20260603-acc-pkg-pilot/proposal.md`
* Capability pool: `openspec/changes/20260603-capability-pool/proposal.md`
* Naming convention: `openspec/RENAMES.md`
