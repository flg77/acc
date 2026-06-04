# 20260604-business-roles-domain-split — design

## 1. Package topology

```
@acc/business-roles@1.0.x   (FROZEN monolith — 25 roles, kept published)
@acc/business-roles@2.0.0   (umbrella — depends_on the 7 below)
        │
        ├── @acc/hr-roles@1.0.0          (3)
        ├── @acc/finance-roles@1.0.0     (3)
        ├── @acc/sales-roles@1.0.0       (6)  ← +3 new
        ├── @acc/marketing-roles@1.0.0   (5)  ← +1 new
        ├── @acc/legal-roles@1.0.0       (2)
        ├── @acc/support-roles@1.0.0     (3)
        └── @acc/operations-roles@1.0.0  (7)
```

Versioning: domain packs start at `1.0.0`; the umbrella is `2.0.0`
(MAJOR — its shape changes from 25 roles to 7 deps).  `^1.0` therefore
does NOT cross to the umbrella — which is *why* the 1.0.x monolith stays
published.

## 2. Transitive dependency resolver (acc-core)

**Problem.** `acc/pkg/install.py::_check_dependencies` raises
`MissingDependency` if a `depends_on` entry isn't already installed, and
the boot-time fetcher (`acc/pkg/fetch.py::fetch_and_install`) installs a
single package.  So an umbrella can't "install one, get seven".

**Solution.** `acc/pkg/fetch.py::fetch_and_install_closure(name, constraint)`:
1. `resolve_constraint` the parent; materialise its tarball.
2. `read_manifest(tarball)` (new helper in `install.py`) → read
   `depends_on`.
3. For each dep not already satisfied (`installed_satisfying`),
   recurse — children install before the parent.
4. Verify + install the parent.
5. `visited` set + `_MAX_CLOSURE_DEPTH` guard cycles/runaway.

Wired at three call sites (replacing `fetch_and_install`):
`collective_cmd.py` `pkg-install` (boot) + `pkg-install-direct`
(operator reconciler), and `assistant_proposal.py` `PROPOSE_INFUSE`.
Leaf packs (no deps) behave exactly as before.

Shared `_verify_and_install()` keeps the signing-floor logic identical
between the single-package and closure paths.

## 3. Domain assignment decisions

* **`revenue_operations_analyst` → finance.**  It owns CRM-data
  quality, forecasting, and pipeline analytics — a finance/RevOps
  function.  The *new* `sales_operations_manager` owns the sales
  operating system (territory/quota/comp/tooling).  Both `seed_context`s
  state the boundary explicitly so they don't collide.
* **`risk_compliance_analyst` → legal.**  Compliance focus; it keeps
  `domain_receptors: [finance_accounting, legal_compliance]` so it still
  answers finance paracrine signals.
* **`operations-roles`** is the residual home for cross-functional ops /
  IT / product roles that don't fit a single GTM domain.

## 4. New role authoring

Each new role mirrors the sibling 3-file shape (`role.yaml` +
`system_prompt.md` + `eval_rubric.yaml`), `persona: formal|concise|analytical`,
`os_basics: true`, `allowed_mcps: [arxiv, wikipedia, web_fetch]`, the
standard 7 `allowed_actions`, `version: "1.0.0"`, `domain_id` set.  No
novel skills/MCPs → no `skill_mcp_tiers.yaml` change → build stays
tier-clean.  `perception_profile` left unset (matches the 25 siblings;
the `domain` profile is a deferred Phase-2 concern).

Differentiation notes baked into seed_context:
* `inside_sales_rep` vs `sales_development_rep` — full remote cycle (incl.
  close) vs pure top-of-funnel handoff.
* `key_account_manager` vs `account_executive` — post-sale lifecycle of
  named accounts vs new-logo close.
* `sales_operations_manager` vs `revenue_operations_analyst` — process/
  tooling design vs data/forecasting.
* `brand_manager` vs `product_marketer`/`content_marketer` — brand-voice
  stewardship vs product GTM / asset production.

## 5. Build & source layout (acc-ecosystem-spearhead)

`--repo-root` repoints `roles/` + `skills/` + `mcps/` + the default
tiers path *together*, so the spearhead must carry all build inputs:

```
acc-ecosystem-spearhead/
├── roles/                       # 25 restored + 4 new
├── manifests/{hr,finance,sales,marketing,legal,support,operations}.yaml
├── tools/build_family_pkg.py    # vendored from acc
├── tools/build_umbrella_pkg.py  # new — hand-builds the umbrella
├── tools/skill_mcp_tiers.yaml   # vendored
├── skills/ · mcps/              # vendored bundled sources
├── secret/                      # RH-Mastery (never published)
├── sync-sources.sh · build-all.sh
└── tests/test_build_families.py # coverage/schema guards
```

The umbrella can't be produced by `build_family_pkg.py` (it always
writes `depends_on: []`), so `build_umbrella_pkg.py` assembles the
manifest by hand and calls `acc.pkg.build.build` directly — a
content-empty tree hashes deterministically.

## 6. RH-Mastery secrecy

Private packs install from a `tier: self`, `mode: file` catalog over a
local dir (+ `.accpkg.sha256` sidecar) under `ACC_ALLOW_UNSIGNED=1`;
never published to the public registry.  See `design-cuopt.md`'s sibling
concern and the spearhead `secret/README.md` for the redistribution
caveat: `rh_mastery` tooling is Apache-2.0 but the mirrored Red Hat doc
PDFs are proprietary and must not be bundled.

## 7. Verification

* acc-core: `tests/pkg/test_fetch_closure.py` (closure installs
  children-then-umbrella; single install raises `MissingDependency`;
  idempotency; leaf behaves like single).
* spearhead: `tests/test_build_families.py` (7 manifests, 29 roles, no
  overlap, every role validates, umbrella deps == 7).
* e2e: build 7 + umbrella → stage as file catalog →
  `fetch_and_install_closure("@acc/business-roles", "^2.0")` installs all
  8; a `^1.0` pin still resolves the frozen monolith.
