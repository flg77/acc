---
type: backlog-item
area: operator-release
id: OR-04
status: in-progress
size: L
depends: [OR-01]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-04 — De-brand and rename the API group

**What.** Module path → `github.com/flg77/acc-operator`; `PROJECT` repo/domain; Containerfile `vendor`/`name`/`version` labels. API group `acc.redhat.io` → **`acc.ic3networks.com`** (D-032), across: `api/v1alpha1` markers, webhook paths, `config/`, CSV, bundle CRD filenames; console plugin `src/models.ts` + `console-extensions.json`; runtime modules `acc/role_crd_loader.py`, `lifecycle.py`, `lifecycle_broker.py`, `deployment.py`, `runtime_evidence_bridge.py`, `runtime_evidence_adapters.py`, `pkg/agent_bom.py` (one constant); `regulatory_layer/runtime/falco-rules.yaml`, `tetragon-tracingpolicy.yaml`; `gitops/argocd/applications/*`; docs.

**Why now.** A CRD group cannot change in place. Before the first public bundle it is a rename; after, it is a second API with a deprecation period. Business plan §9 lists this as the risk to settle before the first upstream release.

**Migration.** CHANGELOG upgrade note: export CRs → uninstall 0.2.x → install 0.3.0 → re-apply under the new group. Done on bb3 and lighthouse.

**Done when.** `git grep -nE 'redhat\.io|redhat-ai-dev|vendor="Red Hat"'` is empty in both operator repos and in `acc-spearhead` outside CHANGELOG history.

**2026-10-04 — code done.** Operator: `flg77/acc-spearhead-operator` branch `or-04-group-rename` (stacked on OR-05 → OR-03): group, module path, vendor label, CRDs regenerated under new names, no `replaces`/`skips`, own `CHANGELOG.md` with the upgrade notes. Runtime: `acc/api_group.py` + seven modules + rules/samples/tests/docs on the acc-spearhead plan branch. Finding: controller-gen's loader walked into the pinned runtime checkout under `bin/` (the runtime's frozen copy of the operator) and regenerated the old group beside the new one — paths pinned to `./api ./cmd ./internal`.

**Open:** the lab migration on bb3 and lighthouse (needs the 0.3.0 build, OR-09); a cluster install under the new group (OR-07 CI).

**2026-10-07 — migration tooling done.** Operator PR #39 (`or-04-lab-migration`): `hack/migrate_api_group.py` (stdlib only, runs on acc1) with `export` → `rewrite --version 0.28.0` → `apply [--dry-run]` → `verify`, plus `rollback`; the rewrite keeps `spec` byte for byte but for the group, the operator's label/annotation keys, `spec.version` (the 0.28.0 floor) and the dropped `manifestDelivery`; `verify` checks every object is back and no PVC was lost or rebound (NATS/Redis volumes come from claim templates and reattach by name; delete the CRs through the old operator, never `--cascade=orphan`, since 0.3.0 adopts nothing). Public runbook `docs/migrate-from-0.2.md`; the lab sequence (bb3 first, then lighthouse; Subscription `acc-operator`, CatalogSource `acc-catalog` in `openshift-marketplace`, old CRDs dropped, `run bundle` 0.3.0) in the withheld `docs/howto-operator-ops.md`. Eleven tests under `test/migration`, run by the CI parity job.

**Still open:** the run itself on bb3 and lighthouse. It needs runtime `v0.28.0` as `acc-agent-core-0.28.0` in the lab repository and the 0.3.0 bundle; both come from the maintainer sequence in the summary (§5 steps 2–5). Then this item closes.
