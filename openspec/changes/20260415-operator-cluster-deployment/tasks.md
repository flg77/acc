# Tasks: ACC Operator — Cluster Deployment Guide & Certification Roadmap

Branch: `feature/ACC-5-operator-deployment-docs`
Commit convention: `[N] type(docs/scope): summary`

All tasks produce committed, reviewable output. Each checkbox = one logical unit of work (one PR-ready commit).

---

## Phase 1 — Foundation: Missing Operator Artifacts

These gaps were identified during the design scan and must be resolved before
the install guide can give accurate instructions.

- [ ] **[1a]** Add `operator/Containerfile` (UBI10 multi-stage build: Go 1.22 builder → ubi10-minimal runtime, UID 65532, labels `vendor`/`name`/`version`/`summary` required by preflight)
- [ ] **[1b]** Add `operator/bundle.Dockerfile` (OLM bundle image definition used by `make bundle-build`)
- [ ] **[1c]** Add `operator/config/crd/bases/acc.redhat.io_agentcorpora.yaml` (hand-written CRD YAML — required for `make install` and `make deploy` to work without running `controller-gen`)
- [ ] **[1d]** Add `operator/config/crd/bases/acc.redhat.io_agentcollectives.yaml`
- [ ] **[1e]** Add `operator/config/crd/kustomization.yaml` (lists both CRD files)
- [ ] **[1f]** Add `operator/config/rbac/kustomization.yaml` (lists role, role_binding, service_account)
- [ ] **[1g]** Add `operator/config/samples/kustomization.yaml` (lists standalone sample)
- [ ] **[1h]** Verify `operator/config/default/kustomization.yaml` references all sub-dirs correctly; fix any missing resource entries

---

## Phase 2 — Core: Write `docs/operator-install-local.md`

- [ ] **[2a]** Write **§1 Capabilities Summary** — what AgentCorpus and AgentCollective do; list of managed components (NATS, Redis, OPA, bridge, OTel); prerequisite detection behaviour; upgrade approval gate; OLM Level 3 maturity claim (≤ 250 words)

- [ ] **[2b]** Write **§2 Prerequisites** — required tools table:

  | Tool | Min version | Install hint |
  |------|------------|--------------|
  | `go` | 1.22 | `dnf install golang` / brew |
  | `kubectl` or `oc` | 1.27 / 4.14 | OCP client download |
  | `kustomize` | v5 | `make kustomize` (auto-downloaded) |
  | `operator-sdk` | v1.36 | `make operator-sdk` (auto-downloaded) |
  | `docker` or `podman` | any | for image build/push |
  | Container registry access | — | quay.io org or internal mirror |
  | Cluster with `cluster-admin` | OCP 4.14+ / k8s 1.27+ | — |

- [ ] **[2c]** Write **§3 Lab Cluster Option Matrix** — CRC setup (memory requirements: 16 GB RAM minimum for NATS + Redis + operator); Kind + OLM install commands; remote OCP node access pattern; explicit note that local Podman (standalone, no Kubernetes) is not supported for operator deployment

- [ ] **[2d]** Write **§4 Build & Push the Operator Image** — commands:
  ```
  cd operator/
  make docker-build IMG=quay.io/<org>/acc-operator:0.1.0
  make docker-push  IMG=quay.io/<org>/acc-operator:0.1.0
  ```
  Include podman alias note for environments without Docker.

- [ ] **[2e]** Write **§5 Method A — Kustomize Deploy** (fastest, no OLM):
  ```
  make install IMG=...         # install CRDs
  make deploy  IMG=...         # deploy operator Deployment
  ```
  Include: namespace creation, verify pod running, known pitfall (StorageClass for NATS PVC).

- [ ] **[2f]** Write **§6 Method B — OLM Bundle Deploy** (mirrors OperatorHub, required pre-certification):
  ```
  make bundle-build BUNDLE_IMG=...
  make bundle-push  BUNDLE_IMG=...
  operator-sdk run bundle <BUNDLE_IMG> --namespace acc-operator-system
  ```
  Include: what OLM creates (CatalogSource, OperatorGroup, Subscription, InstallPlan), how to watch progress (`kubectl get installplan`), cert-manager requirement for webhooks on vanilla k8s.

- [ ] **[2g]** Write **§7 Method C — Internal CatalogSource** (offline / enterprise lab):
  ```
  opm index add --bundles <BUNDLE_IMG> --tag <INDEX_IMG>
  # Create CatalogSource CR pointing at INDEX_IMG
  # Create OperatorGroup + Subscription
  ```
  Include the `CatalogSource` CR YAML inline; note that `opm` requires the bundle image to be accessible from the cluster.

- [ ] **[2h]** Write **§8 Create the Category-A WASM ConfigMap** — prerequisite before any AgentCorpus can pass validation:
  ```
  kubectl create configmap acc-cat-a-wasm \
    --from-file=category_a.wasm=./path/to/category_a.wasm \
    -n <corpus-namespace>
  ```
  Note: WASM blob generation is out of scope; placeholder wasm accepted for testing.

- [ ] **[2i]** Write **§9 Deploy a Sample AgentCorpus** — apply `config/samples/acc_v1alpha1_agentcorpus_standalone.yaml` then `acc_v1alpha1_agentcollective.yaml` (note: collective must be created separately); watch events.

- [ ] **[2j]** Write **§10 Verify Installation** — verification checklist:
  - `kubectl get agentcorpus -n acc-system` → `Phase: Ready`
  - `kubectl get agentcollective -n acc-system` → `Phase: Ready`
  - `kubectl get pods -n acc-system` → NATS, Redis, OPA bundle, agent pods all Running
  - `kubectl describe agentcorpus sol-corpus` → conditions table all True
  - NATS JetStream probe: `kubectl exec -n acc-system <nats-pod> -- nats stream ls`

- [ ] **[2k]** Write **§11 Uninstall**:
  - Method A: `make undeploy && make uninstall`
  - Method B: `operator-sdk cleanup acc-operator --namespace acc-operator-system`
  - Note: PVCs are not deleted automatically — manual cleanup required

---

## Phase 3 — Core: Write `docs/operator-certification.md`

- [ ] **[3a]** Write **§1 Overview & Catalog Targets** — distinguish:
  - *Community operators* (community-operators GitHub repo, no Red Hat review, faster)
  - *Certified operators* (Red Hat Connect partner portal, full review, appears in embedded OperatorHub)
  - Recommendation: submit to community-operators first as tech preview, then pursue certified

- [ ] **[3b]** Write **§2 Prerequisites** — Red Hat Technology Partner account (connect.redhat.com), quay.io org with `acc-operator` and `acc-operator-bundle` repositories set to public, `preflight` CLI installed (`go install github.com/redhat-openshift-ecosystem/openshift-preflight@latest`)

- [ ] **[3c]** Write **§3 Step 1 — Preflight Checks** with exact commands:
  ```bash
  # Check operator image
  preflight check container quay.io/<org>/acc-operator:0.1.0 \
    --pyxis-api-token <token>

  # Check bundle
  preflight check operator quay.io/<org>/acc-operator-bundle:0.1.0 \
    --kubeconfig ~/.kube/config \
    --namespace acc-operator-system \
    --serviceaccount acc-operator-controller-manager
  ```
  List the 12 standard preflight checks and which ones ACC-specific issues are most likely to hit (image labels, `runAsNonRoot`, no `COPY --chown root`).

- [ ] **[3d]** Write **§4 Step 2 — Red Hat Connect Submission** — navigate connect.redhat.com → Software → Operators → Create project; fill in product name, description, bundle image SHA; attach scorecard results; initiate certification request.

- [ ] **[3e]** Write **§5 Step 3 — Certification Pipeline (Konflux/HACBS)** — what the automated pipeline checks (OCP version matrix 4.14–4.18, scorecard, preflight re-run); how to monitor via the Connect portal; how to resubmit after a fix without creating a new project.

- [ ] **[3f]** Write **§6 Step 4 — Review & Publication** — Red Hat reviewer SLA (5–10 business days for first submission); types of feedback (blocking vs. advisory); how a re-review is triggered; what the OperatorHub listing looks like after approval.

- [ ] **[3g]** Write **§7 Timeline & Common Failures** table:

  | Failure | Preflight check | Fix |
  |---------|----------------|-----|
  | Missing image labels | `HasRequiredLabel` | Add `vendor`, `name`, `version`, `summary` to Containerfile |
  | Root filesystem write | `RunAsNonRoot` | Already fixed (UID 65532 in manager.yaml) |
  | Privileged container | `HasNoProhibitedContainerSpec` | Remove any `privileged: true` |
  | CSV missing icon | `ValidateOperatorBundle` | Add base64 SVG to CSV `spec.icon` |
  | OwnNamespace not in installModes | `BundleIndexImageAnnotations` | Already set in CSV |
  | scorecard timeout | Scorecard suite | Provide live `kubeconfig` to `operator-sdk scorecard` |

- [ ] **[3h]** Write **§8 Tech Preview Path via community-operators** — fork `k8s-operatorhub/community-operators`, add `operators/acc-operator/0.1.0/` bundle, open a PR; automated CI (operator-courier) validates; no Red Hat reviewer required; appears on operatorhub.io within 24 h of merge.

---

## Phase 4 — Integration

- [ ] **[4a]** Add cross-references: link `operator-install-local.md` from the root `README.md` (or `docs/CHANGELOG.md`); link both docs from `openspec/changes/20260415-operator-cluster-deployment/` in the summary
- [ ] **[4b]** Add `> ℹ️ Verify against current Red Hat Connect UI` callouts to §3d, §3e, §3f of certification doc (Red Hat portal UI changes regularly)
- [ ] **[4c]** Review `operator/config/default/kustomization.yaml` — ensure the `manager_auth_proxy_patch.yaml` reference is resolved or removed (file not yet created; guard with comment until auth proxy is added in a future change)

---

## Phase 5 — Polish

- [ ] **[5a]** Proof-read both documents for command accuracy against actual `operator/Makefile` targets
- [ ] **[5b]** Add a `docs/operator-install-local.md` quick-reference table at the top (3-column: what you want / method / command)
- [ ] **[5c]** Update `docs/CHANGELOG.md` with ACC-5 entry
- [ ] **[5d]** Commit on `feature/ACC-5-operator-deployment-docs`, open PR against `main`

---

## Task Summary

| Phase | Tasks | Deliverable |
|-------|-------|-------------|
| 1 — Foundation | 8 | Missing operator config artifacts (CRDs, Dockerfiles, kustomizations) |
| 2 — Install Guide | 11 | `docs/operator-install-local.md` (~400 lines) |
| 3 — Cert Guide | 8 | `docs/operator-certification.md` (~250 lines) |
| 4 — Integration | 3 | Cross-references, callouts, config fix |
| 5 — Polish | 4 | Review, quick-ref table, changelog, PR |
| **Total** | **34** | |
