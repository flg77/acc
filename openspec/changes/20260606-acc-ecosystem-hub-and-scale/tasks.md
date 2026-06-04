# 20260606-acc-ecosystem-hub-and-scale — tasks

Six sub-slices that can ship independently.  Recommended order
when the operator is ready to engage Stage 2:
**2.4 → 2.6 → 2.1 → 2.3 → 2.2 → 2.5**

The reasoning: 2.4 + 2.6 are pure work inside this repo and can
ship without any external coordination.  2.1 + 2.3 unlock the
public surfaces.  2.2 (family extractions) is the
high-blast-radius move that goes last.  2.5 (Podman Desktop
plugin) can run in parallel any time after 2.4.

## Sub-slice 2.4 — Marketplace + Catalog admin TUI/WebGUI

### 2.4.1 `acc/tui/screens/marketplace.py`
- [ ] Pkg-aware sibling to `ecosystem.py`; lists catalog availability
- [ ] Tier badge column (trusted / tp / community / self)
- [ ] Signer column (truncated identity)
- [ ] One-tap install: posts a synthesised PROPOSE_INFUSE to the
      Compliance pane's Package Proposals queue (PR #32)

### 2.4.2 `acc/tui/screens/catalogs.py`
- [ ] List / add / remove / re-prioritise catalogs
- [ ] Renders to `<workspace>/.acc/catalogs.yaml` via
      `acc._atomic_write.atomic_write_text`
- [ ] Validates entries against `acc.pkg.catalog.Catalog` Pydantic
      model before commit

### 2.4.3 `acc/tui/screens/ecosystem.py` modify
- [ ] List shows in-tree + installed-package roles together
- [ ] Adds `source` column reading from `CapabilityIndex` (the
      field shipped in PR #21)

### 2.4.4 `acc/webgui/routes_roles.py`
- [ ] GET `/api/roles/available` — proxies `list_available()` from catalog
- [ ] POST `/api/roles/install` — schedules PROPOSE_INFUSE
- [ ] GET / POST `/api/catalogs/` — catalog admin
- [ ] Tests in `tests/webgui/`

### 2.4.5 React surface
- [ ] `acc/webgui/react/marketplace/` — pages mirroring the TUI
- [ ] `acc/webgui/react/catalogs/` — admin form
- [ ] Wire into `acc-web` repo navigation

### Verification (sub-slice 2.4)
- [ ] `pytest tests/tui/ tests/webgui/ --no-cov -q` — ~50 new tests
- [ ] Full sweep stays green

## Sub-slice 2.6 — Docs

### 2.6.1 `docs/CONTRIBUTING-ROLE.md`
- [ ] First-time contributor walkthrough: `acc-pkg init` → write
      evals → cosign sign → publish in under an hour
- [ ] OIDC keyless setup (GitHub Actions identity)
- [ ] Eval format reference (links to `acc/pkg/evals.py`)

### 2.6.2 `docs/MIGRATING-FROM-INTREE.md`
- [ ] Two-release deprecation walk-through
- [ ] `git checkout` recovery for operators who miss the migration window
- [ ] Sample `collective.yaml` with `required_packages:` pinning

### Verification (sub-slice 2.6)
- [ ] Operator review — "I can follow this and ship a community package"

## Sub-slice 2.1 — `flg77/acc-ecosystem` repo bootstrap

### 2.1.1 Operator-side (manual)
- [ ] `gh repo create flg77/acc-ecosystem-spearhead --private`
- [ ] `gh repo create flg77/acc-ecosystem --public`
- [ ] Repoint `acc-promote` to handle the new spearhead→mirror pair

### 2.1.2 Repo content
- [ ] `LICENSE` (Apache 2.0)
- [ ] `CONTRIBUTING.md` — points at `acc/docs/CONTRIBUTING-ROLE.md` (2.6.1)
- [ ] DCO / CLA mechanism per the operator decision
- [ ] `.github/workflows/build.yml` referencing
      `gitops/tekton/pipelines/accpkg-build.yaml` from PR #27
- [ ] Empty `packages/` directory; family extractions land per 2.2

## Sub-slice 2.3 — Public hub at `acc-roles.dev`

### 2.3.1 DNS + hosting
- [ ] `acc-roles.dev` DNS managed by ACC team
- [ ] Hosting choice per the operator decision (Pages / S3 / CloudFront)
- [ ] TLS cert (Let's Encrypt for Pages; ACM for S3)

### 2.3.2 Static index + blob storage
- [ ] `acc-roles.dev/index.json` — same schema as
      `acc/pkg/catalog._fetch_index_https` expects
- [ ] Blob storage path: `/packages/<scope>/<name>-<version>.{accpkg,sig,pem}`
- [ ] Sync from `acc-ecosystem` repo releases (or direct upload from
      Stage 1.3's `acc-pkg publish`)

### 2.3.3 Default catalog config update
- [ ] `examples/catalogs.yaml` `acc-canonical` entry switches URL
      from `acc-hub.acc1.internal` to `acc-roles.dev`

## Sub-slice 2.2 — Family extractions

### 2.2.1 Build the four packages from the in-tree source
- [ ] `@acc/workspace-roles@1.0.0` from `roles/coding_agent*`
- [ ] `@acc/research-roles@1.0.0` from `roles/research_*`
- [ ] `@acc/business-roles@1.0.0` from 30 business roles
- [ ] `@acc/devops-roles@1.0.0` from `data_engineer`,
      `devops_engineer`, `ml_engineer`, `security_analyst`
- [ ] Each runs `tools/build_pilot_pkg.py` extended to accept a list of roles

### 2.2.2 Sign + publish
- [ ] Konflux pipeline publishes each via `acc-pkg publish` against `acc-roles.dev`
- [ ] EC policy (Stage 1.2) attestations populated for each

### 2.2.3 Stage 2 release N — soft deprecation
- [ ] In-tree dirs still present
- [ ] `acc.role_loader` emits `DeprecationWarning` on load for the 44 movable role names
- [ ] `roles/<name>/role.yaml` files gain a header comment pointing at the migrated package

### 2.2.4 Stage 2 release N+1 — hard removal
- [ ] Delete the 44 in-tree role dirs (git history preserves them)
- [ ] Update `tests/conftest.py` movable-role fixtures to consume from a local file catalog
- [ ] Update `samples/collective-*.yaml` to declare `required_packages:`

## Sub-slice 2.5 — `flg77/acc-podman-desktop`

### 2.5.1 Repo bootstrap
- [ ] `gh repo create flg77/acc-podman-desktop --public`
- [ ] TypeScript + Podman Desktop extension API scaffolding
- [ ] Apache 2.0

### 2.5.2 First-run wizard
- [ ] Curated-LLM picker (default RHOAI panel from `ACC_RHOAI_PANEL_PATH`)
- [ ] "Bring your own model" path one click further
- [ ] Posts to the user's `~/.acc/catalogs.yaml`

### 2.5.3 Marketplace integration
- [ ] Shells out to `acc-pkg list --available` for the catalog
      view
- [ ] One-tap install via `acc-pkg install`
- [ ] Compliance pane appears as a separate Podman Desktop tab

### 2.5.4 Podman Desktop marketplace registration
- [ ] Red Hat-operated extension marketplace listing
- [ ] Sign-off from Podman Desktop team

## Open strategic decisions (block sub-slice starts)

- [ ] Q1: Hub hosting authority — single canonical (recommended) vs federated
- [ ] Q2: Verified-Publisher pricing — free for OSS (recommended)
- [ ] Q3: Family boundaries — devops as own family vs rolling into business
- [ ] Q4: CLA mechanism — DCO sign-off (recommended) vs full CLA Assistant
