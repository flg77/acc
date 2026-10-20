---
type: backlog-item
area: operator-release
id: OR-10
status: done
size: M
depends: [OR-09]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-10 — Docs for a stranger

**What.** `acc-operator/README.md`: one sentence (govern, gate, audit), a 15-minute kind quickstart, install from OperatorHub, the compatibility table (operator ↔ `ACC_RUNTIME_VERSION` ↔ OCP/K8s). Move `docs/operator-install-local.md`, `operator-certification.md`, `operator-agentset-guide.md`, `operator-standalone-parity.md`; spearhead keeps a pointer page. `SECURITY.md` with a contact, `CONTRIBUTING.md` with the 10-minute dev setup.

**Done when.** One person who has never seen ACC installs it on kind from the README without help.

**2026-10-05 — done** (acc-spearhead-operator, branch `or-10-docs`, stacked on OR-09). The operator `README.md` is written for a public reader: one sentence (govern, gate, audit), the four resources, a compatibility table (0.3.0 ↔ runtime 0.27.0 ↔ Kubernetes 1.27+ / OpenShift 4.14+ / OLM 0.28+), install from OperatorHub (kubectl one-liner; OpenShift console or Subscription on `candidate-v0`), a 15-minute kind quickstart (OLM, `run bundle` from the public bundle, the Category-A ConfigMap, the standalone sample to Ready), what you get, the console plugin, the docs index, `cosign verify`, build and test. The spearhead-only status table moved to the withheld `docs/SPEARHEAD.md` (new MIRROR-WITHHELD row; `RELEASING.md` §4 no longer points at a withheld file). The four docs moved and were rewritten against the code as it is in 0.3.0: `docs/install.md` (OperatorHub / bundle / own catalog / kustomize — the last needs a webhook certificate source, OLM provides it for the others; the Category-A ConfigMap is a required volume of every agent pod; verify; uninstall), `docs/agentcollective-guide.md` (field map, roles from the embedded tree or packages, per-agent model via `extraEnv`, `roleDefinition` fields as they exist, KEDA, `AccCatalog`/`AccPackageInstall`), `docs/standalone-parity.md` (what keeps the paths in step incl. `test/parity`, the gap table incl. the stale `known_roles.txt`), `docs/catalogs-and-certification.md` (the two community catalogs and what they check; the certified path trimmed to what is current and what would change here). New: `CONTRIBUTING.md` (ten-minute setup, layout, the rules that save a review round, the local e2e), `SECURITY.md` (private reporting + email, supported versions, what to verify: image signatures, package signers, RBAC, webhooks), `LICENSE` (Apache-2.0, was missing). Deleted the stale `docs/WS-A-olm-bundle-runbook.md`. The spearhead keeps `docs/operator.md` as the pointer page; README and `howto-rhoai.md` link to it. The README's edge/RHOAI quickstarts still reference `operator/` — OR-11. Not verified by a stranger yet: the "done when" is a person installing on kind from the README; the quickstart is the e2e's path plus the Category-A ConfigMap and the public images, which exist after OR-09's tag.
