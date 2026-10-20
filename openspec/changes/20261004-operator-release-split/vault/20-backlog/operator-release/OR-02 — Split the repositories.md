---
type: backlog-item
area: operator-release
id: OR-02
status: done
size: M
depends: [OR-01]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-02 — Split the repositories

**What.** `git subtree split -P operator` into private `flg77/acc-spearhead-operator` with history; `console-plugin/` and the two parity tests (`tests/test_operator_crd_bundle_sync.py`, `tests/test_console_plugin_models_parity.py`) move with it. Public `flg77/acc-operator` receives a curated squash per release, exactly the `flg77/acc` discipline.

**Withheld list** (the operator's own `MIRROR-WITHHELD.md`): `hack/build-remote.sh`, `hack/deploy-private-catalog.sh`, `hack/acc-operator-ops.sh`, `config/private-catalog/`, `docs/private-quay-to-ocp-catalog-deploy.md`, `docs/rhoai-e2e-agentset-runbook.md`, `config/samples/loan-origination/`. Delete (not withhold) `bundle_tmp*`, `index_build_tmp*`, `index.Dockerfile*`.

**Also.** `acc-promote` learns the second spearhead → mirror pair.

**Done when.** The private repo builds the same binary as `acc-spearhead/operator` at the split commit; the public repo exists, empty until OR-04 lands.

**2026-10-04 — progress.** The split is assembled on branch `split/acc-spearhead-operator` of `acc-spearhead` (169 commits: the operator's history at the root, `console-plugin/` subtree-added with its own, `test/parity/` with the two moved tests, `docs/MIRROR-WITHHELD.md`, its own `.gitignore`, scratch dirs removed, the RHOAI dashboard link repointed off a withheld doc). **Blocked:** the session's GitHub App cannot create repositories (403). By hand: create private `flg77/acc-spearhead-operator` and (private until OR-09) `flg77/acc-operator`, then

```bash
git fetch origin split/acc-spearhead-operator
git push git@github.com:flg77/acc-spearhead-operator.git origin/split/acc-spearhead-operator:refs/heads/main
```

Then teach `acc-promote` the second pair and delete the handover branch.

**2026-10-04 — done.** Both repositories created by the maintainer; `main` of `flg77/acc-spearhead-operator` pushed from the handover branch (170 commits). Open: `acc-promote` learns the second pair; delete `split/acc-spearhead-operator` from acc-spearhead.
