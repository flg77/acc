# 20260604-business-roles-domain-split — proposal

## Why

Stage 2 extracted the 25 corporate roles into a single
`@acc/business-roles` family pack.  A monolith is the wrong unit for
this catalog: an operator who wants a finance collective shouldn't
install HR, IT, and sales personas too; a team can't grow the sales
roster without re-releasing 25 unrelated roles; and the pack mixes
knowledge domains that have nothing to do with each other.

Split `@acc/business-roles` into **seven per-domain packs** so users
install only the domains they need and can version each independently.
The roles already carry `domain_id` tags (`sales_revenue`, `marketing`,
`finance_accounting`, `people_hr`, `legal_compliance`,
`customer_success`, …), so the seam already exists.

Three adjacent needs ride along:

1. **Four missing corporate roles** the operator needs don't exist yet:
   `key_account_manager`, `inside_sales_rep`, `sales_operations_manager`
   (sales), `brand_manager` (marketing).
2. **RH-Mastery** is a separate vertical agentset that must stay
   **secret/local** — it consumes Red Hat customer documentation that
   can't be redistributed.  The split is the moment to establish the
   private-pack mechanism.
3. **cuOpt enrichment** — several corporate roles (sales ops, finance,
   support, marketing, HR) are optimization problems (territory/quota,
   budget/portfolio, rostering).  NVIDIA cuOpt (Apache-2.0, GPU
   VRP/LP/MILP) can power them.  Designed now, shipped later
   (rhoai/GPU-gated).

## What changes

### Seven domain packs (replaces the monolith)

| Pack (`1.0.0`) | Roles |
|---|---|
| `@acc/hr-roles` | hr_business_partner, learning_development_specialist, recruiter |
| `@acc/finance-roles` | financial_analyst, fpa_analyst, revenue_operations_analyst |
| `@acc/sales-roles` | account_executive, sales_development_rep, sales_engineer, **key_account_manager**, **inside_sales_rep**, **sales_operations_manager** |
| `@acc/marketing-roles` | content_marketer, demand_generation_specialist, marketing_analyst, product_marketer, **brand_manager** |
| `@acc/legal-roles` | contract_analyst, risk_compliance_analyst |
| `@acc/support-roles` | customer_success_manager, customer_support_agent, technical_support_specialist |
| `@acc/operations-roles` | business_analyst, operations_analyst, procurement_specialist, project_manager, product_manager, it_operations_specialist, it_support_specialist |

29 roles = 25 existing + 4 new.  `revenue_operations_analyst` →
finance (data/forecasting); the new `sales_operations_manager` is the
sales-side process/tooling owner.  `risk_compliance_analyst` → legal
(keeps its `finance_accounting` receptor).

### Umbrella + backward-compat

* `@acc/business-roles@2.0.0` becomes an **umbrella meta-pack** —
  carries no roles, only `depends_on` the seven.
* acc-core gains a **transitive `depends_on` resolver**
  (`fetch_and_install_closure`) so installing the umbrella pulls all
  seven in dependency order from one `required_packages:` entry.
* The frozen **`@acc/business-roles@1.0.x` (25-role monolith) stays
  published**, so existing `@acc/business-roles@^1.0` pins keep
  resolving unchanged.  No forced migration.

### Source home

Editable role sources move to a new **private**
`flg77/acc-ecosystem-spearhead` repo (mirrors `acc-spearhead` →
`flg77/acc`).  Per-domain packs build there; built artifacts publish to
the public `flg77/acc-ecosystem`.  RH-Mastery secret sources live there
under `secret/`.

### cuOpt (design only — see `design-cuopt.md`)

Spec a thin cuOpt MCP wrapper + role→use-case map, gated to
`rhoai`/GPU.  No code this round.

## Impact

* **acc-core (this repo):** `acc/pkg/fetch.py` (closure resolver),
  `acc/pkg/install.py` (`read_manifest`), `acc/cli/collective_cmd.py`
  + `acc/assistant_proposal.py` (call closure).  Tests:
  `tests/pkg/test_fetch_closure.py` (+ mock repoint in
  `test_propose_infuse.py`, `test_collective_cmd_pkg.py`).
* **acc-ecosystem-spearhead (new private):** role sources, 7 manifests,
  `build_umbrella_pkg.py`, vendored build inputs, coverage tests.
* **acc-ecosystem (public):** 7 packs + umbrella added; frozen monolith
  kept; `index.json` regenerated (12 entries); README refreshed.
* **Docs:** `examples/catalogs.yaml`, README, `PUBLISHING-FAMILY-PACKS.md`,
  `MIGRATING-FROM-INTREE.md`.
* **No new env knobs.**  No role-schema change (the 30-field
  `RoleDefinitionConfig` already supports per-domain roles).

## Backward-compat & risks

* `^1.0` pins keep the monolith (kept published); `^2.0` opts into the
  umbrella.  The umbrella's value depends on the new transitive
  resolver — without it, the installer's `_check_dependencies` would
  refuse the umbrella (children absent).  Resolver + tests included.
* `revenue_operations_analyst` vs `sales_operations_manager` overlap —
  differentiated in both `seed_context`s (data/forecasting vs
  process/tooling).
* Build needs `acc` importable in the spearhead — `sync-sources.sh` +
  `pip install -e` (or PYTHONPATH) documented.

## What stays open

* cuOpt MCP wrapper implementation (`design-cuopt.md`) — follow-up change.
* RH-Mastery role authoring — its own brainstorm; this change only lands
  the private-pack mechanism + redistribution caveat.

## References

* Design: `design.md`, `design-cuopt.md`, `tasks.md`
* Vault brainstorm: `<vault>/ACC Openspec/ACC Role Proposals/business-roles-domain-split/`
* Build tooling: `tools/build_family_pkg.py`, spearhead `tools/build_umbrella_pkg.py`
* Stage 2 split: `openspec/changes/20260606-acc-ecosystem-hub-and-scale/proposal.md`
