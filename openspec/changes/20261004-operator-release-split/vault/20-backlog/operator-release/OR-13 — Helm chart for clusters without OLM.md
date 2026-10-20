---
type: backlog-item
area: operator-release
id: OR-13
status: code done
size: M
depends: [OR-09]
created: 2026-10-05
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-13 — Helm chart for clusters without OLM

**Why.** operatorhub.io reaches only clusters that run OLM; most upstream Kubernetes clusters do not. The business plan's adoption channel is `pip install` + Helm (§5 items 1, 4). A chart of the operator on ArtifactHub is that channel.

**What.** `charts/acc-operator/` in `flg77/acc-operator`, generated from the same `config/` the bundle comes from (`make build-installer` → `dist/install.yaml` is the raw material): CRDs under `crds/`, RBAC, the manager Deployment with `image.repository`/`image.tag`/`consolePlugin.image` values, the webhook configurations, and **a certificate source**, which is the one thing OLM gives us for free and a chart must provide: `webhooks.certManager.enabled` (default on: `Issuer` + `Certificate` + `cert-manager.io/inject-ca-from`), with `webhooks.enabled=false` as the escape hatch for a cluster without cert-manager (then no defaulting: `spec.version` and the assistant injection are the user's job, and the typo check is gone). `make helm` renders and lints; `helm template` output is compared against `dist/install.yaml` in `ci`; `e2e` gains a kind job that installs the chart (cert-manager from its manifest) and runs the same checks as the OLM job. Publish: `helm package` + an OCI push to `quay.io/flg77/charts/acc-operator` in `release.yml`, and the ArtifactHub `artifacthub-repo.yml`.

**Decisions to take first.** (1) Chart CRD handling: `crds/` (helm installs once, never upgrades) versus templated CRDs with `--skip-crds` guidance; recommend `crds/` plus a documented `kubectl apply -f` upgrade step, as most operators do. (2) Whether the chart installs OLM-free *and* console-plugin-free (yes: the runnable detects the console API at run time already).

**Size.** M: a day for the chart and lint, a day for the e2e job and the release step, half a day for ArtifactHub.

**Done when.** `helm install acc-operator oci://quay.io/flg77/charts/acc-operator` on a kind cluster with cert-manager yields the same Ready corpus the OLM quickstart does, and the chart is listed on ArtifactHub.

**2026-10-05 — code done** (acc-spearhead-operator PR #29, branch `or-13-helm-chart`; `ci` with the new `helm` job and `e2e` with the new `kind-helm` job green after one fix: the health step had asked for the Certificate under the Secret's name). `charts/acc-operator-chart`: CRDs in `crds/` (installed once; the README says how to apply a new version's CRDs first), the manager with the same args/env as `config/manager/manager.yaml`, RBAC, the webhook Service, the webhook configurations, and the certificate OLM otherwise provides: cert-manager `Issuer` (self-signed, or `webhooks.certManager.issuerRef`) + `Certificate`, CA injected into the configurations, Secret mounted at controller-runtime's default directory; `webhooks.enabled=false` as the escape hatch, documented with what it costs. Decisions taken as the note proposed: `crds/` + documented apply step; the chart installs OLM-free and console-plugin-free (the runnable detects the console API). Generated from `config/` by `hack/helm-sync.py` (`make helm-sync`; `make helm-check` in `ci`; `make helm-lint` renders defaults, webhooks off, own certificate); rendered with defaults it equals the kustomize installer in args, env, RBAC rules and webhook set. `e2e` gains a kind job without OLM (cert-manager, `helm install --wait`, webhooks through the cert-manager certificate, samples admit, uninstall keeps the CRDs). `release.yml` pushes `oci://quay.io/flg77/acc-operator-chart:<version>`, signs it with the images, attaches the package. Named `acc-operator-chart` because quay.io repositories are flat and `acc-operator` is the image. helm is built from the Go module proxy. **Maintainer steps:** create the public quay.io repository `flg77/acc-operator-chart`; after the first release, add the OCI repository on ArtifactHub (RELEASING.md §5b).
