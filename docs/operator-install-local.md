# ACC Operator — Installation Guide

> **Source:** `operator/` · **Version:** 0.1.0 · **OLM maturity:** Level 3 — Seamless Upgrades

## Quick Reference

| Goal | Method | Key command |
|------|--------|-------------|
| Fastest dev deploy | A — Kustomize | `make deploy IMG=...` |
| Validate OLM flow (required before cert) | B — OLM bundle | `operator-sdk run bundle <BUNDLE_IMG>` |
| Enterprise / offline lab | C — CatalogSource | `kubectl apply -f catalogsource.yaml` |

---

## 1. Capabilities Summary

The **Agentic Cell Corpus (ACC) Operator** manages the full lifecycle of biologically-inspired
multi-agent AI deployments on OpenShift 4.14+ and Kubernetes 1.27+.

**What it installs and manages:**

| Component | Resource type | Notes |
|-----------|--------------|-------|
| NATS JetStream | `StatefulSet` | 1-node or 3-node clustered; JetStream enabled |
| Redis | `StatefulSet` | 1-node standalone or 3-node Sentinel |
| OPA Bundle Server | `Deployment` | Serves Category-B live-updatable policy bundles |
| NATS-Kafka Bridge | `Deployment` | Only deployed when Kafka is reachable (TCP probe) |
| OTel Collector | `Deployment` | Only deployed when `observability.backend=otel` |
| Agent Deployments | `Deployment` × 5 roles | ingester, analyst, synthesizer, arbiter, observer |
| KEDA ScaledObjects | `ScaledObject` (CRD) | Per-role autoscaling; skipped if KEDA absent |
| KServe InferenceService | `InferenceService` (CRD) | vLLM/Llama Stack backends; skipped if KServe absent |
| PrometheusRules | `PrometheusRule` (CRD) | 3 alert groups; skipped if Prometheus Operator absent |
| OPA ConstraintTemplates | `ConstraintTemplate` (CRD) | 3 CTs; skipped if Gatekeeper absent |

**What it does NOT install:** Kafka, KEDA, OPA Gatekeeper, RHOAI/KServe, Milvus, Prometheus
Operator. These are cluster prerequisites — the operator detects them, emits Warning events when
absent, and degrades gracefully (no hard failure).

**Upgrade approval gate:** When `spec.upgradePolicy.requireApproval: true` and NATS or Redis
version changes, the operator halts and waits for:
```
kubectl annotate agentcorpus <name> acc.redhat.io/approve-upgrade=<version>
```

---

## 2. Prerequisites

### Tools

| Tool | Min version | How to obtain |
|------|------------|--------------|
| `go` | 1.22 | `dnf install golang` / `brew install go` |
| `kubectl` **or** `oc` | 1.27 / OCP 4.14 | [OCP client download](https://console.redhat.com/openshift/downloads) |
| `kustomize` | v5 | Auto-downloaded by `make kustomize` |
| `operator-sdk` | v1.36 | Auto-downloaded by `make operator-sdk` |
| `docker` **or** `podman` | any | For image build/push |
| Container registry | — | `quay.io/<org>` or internal mirror |

> **Podman alias:** if you use Podman instead of Docker, run `alias docker=podman` before any `make docker-*` targets, or use `podman build` / `podman push` directly.

### Cluster

- OpenShift 4.14+ **or** Kubernetes 1.27+ with OLM installed
- `cluster-admin` privileges (required for CRD + ClusterRole installation)
- A default `StorageClass` that provides `ReadWriteOnce` PVCs (for NATS and Redis)
- At least **16 GiB RAM** available on worker nodes when running full stack (NATS + Redis + 5 agent pods)

---

## 3. Lab Cluster Option Matrix

| Option | Kubernetes version | OLM included | Webhook cert injection | Suitable for |
|--------|--------------------|--------------|----------------------|--------------|
| **CRC (OpenShift Local) ≥ 2.38** ✅ recommended | OCP 4.14 | ✅ built-in | ✅ built-in | All methods A, B, C |
| **Remote OCP 4.14+ node** | OCP 4.14 | ✅ built-in | ✅ built-in | Integration + cert testing |
| **Kind + OLM** | k8s 1.27 | ⚠️ manual install | ⚠️ needs cert-manager | Method A only (easier) |
| **Local Podman (no Kubernetes)** | ❌ none | ❌ | ❌ | **Not supported** — Podman-only; use for Python agent smoke tests only |

### CRC Setup (recommended lab)

```bash
# Install CRC (OpenShift Local) — download from https://console.redhat.com/openshift/create/local
crc setup
crc config set memory 16384   # 16 GiB minimum
crc config set cpus 6
crc start --pull-secret-file pull-secret.txt

# Configure CLI
eval $(crc oc-env)
oc login -u kubeadmin https://api.crc.testing:6443
```

### Kind + OLM Setup (pure Kubernetes)

```bash
kind create cluster --name acc-lab
# Install OLM
curl -sL https://github.com/operator-framework/operator-lifecycle-manager/releases/download/v0.28.0/install.sh | bash -s v0.28.0
# Install cert-manager (required for webhook TLS on vanilla k8s)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=120s
```

---

## 4. Build & Push the Operator Image

All commands run from the `operator/` directory:

```bash
cd operator/

# Set your registry target (adjust org as needed)
export IMG=quay.io/<your-org>/acc-operator:0.1.0
export BUNDLE_IMG=quay.io/<your-org>/acc-operator-bundle:0.1.0

# Build the operator image (uses operator/Containerfile)
make docker-build IMG=$IMG

# Push to registry
make docker-push IMG=$IMG
```

> **Note:** The operator image must be **publicly readable** by the cluster during initial testing,
> or you must create an `imagePullSecret` and add it to the `acc-operator-controller-manager`
> ServiceAccount before deploying.

---

## 5. Method A — Kustomize Deploy (Fastest)

Best for: developers iterating on the operator itself. Bypasses OLM — no OperatorGroup or
Subscription is created.

```bash
cd operator/

# 1. Create the operator namespace
kubectl create namespace acc-operator-system

# 2. Install CRDs
make install

# 3. Deploy the operator (creates Deployment, ClusterRole, ServiceAccount, etc.)
make deploy IMG=$IMG

# 4. Watch the operator pod come up
kubectl get pods -n acc-operator-system -w
```

**Expected output:**
```
NAME                                               READY   STATUS    RESTARTS
acc-operator-controller-manager-7d9f6b8c5-x2r4p   1/1     Running   0
```

> **Webhook TLS on vanilla k8s:** The manager requires a TLS certificate for the admission
> webhooks. On OpenShift this is injected automatically. On Kind, install `cert-manager` first
> (see §3) and add the `cert-manager.io/inject-ca-from` annotation to the webhook configurations.

To tear down:
```bash
make undeploy
make uninstall
```

---

## 6. Method B — OLM Bundle Deploy (Mirrors OperatorHub)

Best for: validating the full OLM install flow before certification submission. This method
exercises the same machinery OperatorHub uses: CatalogSource → OperatorGroup → Subscription →
InstallPlan → CSV → operator Deployment.

```bash
cd operator/

# 1. Build and push the bundle image
make bundle-build BUNDLE_IMG=$BUNDLE_IMG
make bundle-push  BUNDLE_IMG=$BUNDLE_IMG

# 2. Run the bundle via operator-sdk (creates temporary CatalogSource + Subscription)
operator-sdk run bundle $BUNDLE_IMG \
  --namespace acc-operator-system \
  --timeout 5m

# 3. Watch OLM process the InstallPlan
kubectl get installplan -n acc-operator-system
kubectl get csv -n acc-operator-system
```

**Expected output:**
```
NAME                         CSV                         APPROVAL    APPROVED
install-abc12                acc-operator.v0.1.0         Automatic   true

NAME                    DISPLAY                          VERSION   REPLACES   PHASE
acc-operator.v0.1.0     Agentic Cell Corpus Operator     0.1.0                Succeeded
```

To clean up:
```bash
operator-sdk cleanup acc-operator --namespace acc-operator-system
```

---

## 7. Method C — Internal CatalogSource (Enterprise / Offline Lab)

Best for: clusters that mirror OperatorHub but have no internet access, or for staging an internal
operator catalog before OperatorHub submission.

```bash
# 1. Build an index image using opm (operator package manager)
#    Install opm: https://github.com/operator-framework/operator-registry/releases
opm index add \
  --bundles $BUNDLE_IMG \
  --tag quay.io/<your-org>/acc-operator-index:0.1.0

docker push quay.io/<your-org>/acc-operator-index:0.1.0

# 2. Create a CatalogSource pointing at the index image
cat <<EOF | kubectl apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: acc-operator-catalog
  namespace: openshift-marketplace   # use olm namespace on vanilla k8s
spec:
  sourceType: grpc
  image: quay.io/<your-org>/acc-operator-index:0.1.0
  displayName: ACC Operator Catalog
  publisher: Red Hat AI Dev
EOF

# 3. Create an OperatorGroup (scopes the operator to acc-operator-system)
cat <<EOF | kubectl apply -f -
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: acc-operator-group
  namespace: acc-operator-system
spec:
  targetNamespaces:
  - acc-operator-system
EOF

# 4. Create a Subscription
cat <<EOF | kubectl apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: acc-operator
  namespace: acc-operator-system
spec:
  channel: alpha
  name: acc-operator
  source: acc-operator-catalog
  sourceNamespace: openshift-marketplace
  installPlanApproval: Automatic
EOF

# 5. Approve the InstallPlan (if installPlanApproval: Manual)
kubectl get installplan -n acc-operator-system
kubectl patch installplan <name> -n acc-operator-system \
  --type merge -p '{"spec":{"approved":true}}'
```

---

## 8. Create the Category-A WASM ConfigMap (Required Prerequisite)

Every `AgentCorpus` references a ConfigMap that holds the `category_a.wasm` blob — the immutable
Category-A governance rule. This ConfigMap **must exist before applying any AgentCorpus CR**,
or the validation webhook will reject it.

```bash
# Option A: use a compiled WASM blob
kubectl create configmap acc-cat-a-wasm \
  --from-file=category_a.wasm=./governance/category_a.wasm \
  -n <corpus-namespace>

# Option B: placeholder for local testing (zero-byte, passes webhook validation)
touch /tmp/category_a.wasm
kubectl create configmap acc-cat-a-wasm \
  --from-file=category_a.wasm=/tmp/category_a.wasm \
  -n acc-system
```

> The WASM blob is compiled from the Category-A OPA policy. Compilation tooling is
> out of scope for this guide — see `governance/` (planned for ACC-6).

---

## 9. Deploy a Sample AgentCorpus

```bash
# 1. Create the corpus namespace
kubectl create namespace acc-system

# 2. Create the WASM ConfigMap (if not done in §8)
touch /tmp/category_a.wasm
kubectl create configmap acc-cat-a-wasm \
  --from-file=category_a.wasm=/tmp/category_a.wasm \
  -n acc-system

# 3. Apply the AgentCollective first (the corpus webhook validates it exists)
kubectl apply -f operator/config/samples/acc_v1alpha1_agentcorpus_standalone.yaml

# 4. Watch events
kubectl describe agentcorpus sol-corpus -n acc-system
kubectl get events -n acc-system --sort-by='.lastTimestamp'
```

The corpus will pass through `Pending → Progressing → Ready` as NATS and Redis StatefulSets
roll out. On a fresh CRC cluster this typically takes 60–120 seconds.

---

## 10. Verify Installation

Run through this checklist before declaring a successful deployment:

```bash
# ✅ Corpus is Ready
kubectl get agentcorpus -n acc-system
# Expected: sol-corpus   standalone   0.1.0   Ready

# ✅ AgentCollective is Ready
kubectl get agentcollective -n acc-system
# Expected: sol-01   sol-01   ollama   Ready

# ✅ Infrastructure pods running
kubectl get pods -n acc-system
# Expected: <corpus>-nats-0        Running
#           <corpus>-redis-0       Running
#           <corpus>-opa-bundle-*  Running
#           sol-01-ingester-*      Running (×2)
#           sol-01-analyst-*       Running (×2)
#           sol-01-synthesizer-*   Running (×1)
#           sol-01-arbiter-*       Running (×1)
#           sol-01-observer-*      Running (×1)

# ✅ Status conditions
kubectl get agentcorpus sol-corpus -n acc-system \
  -o jsonpath='{.status.conditions}' | jq .
# Expected: Ready=True, InfrastructureReady=True, CollectivesReady=True

# ✅ NATS JetStream probe
NATS_POD=$(kubectl get pods -n acc-system -l app.kubernetes.io/component=nats -o name | head -1)
kubectl exec -n acc-system $NATS_POD -- nats stream ls 2>/dev/null || \
  echo "NATS CLI not in image — check NATS HTTP monitor instead:"
kubectl port-forward -n acc-system $NATS_POD 8222:8222 &
curl -s http://localhost:8222/jsz | jq '.streams'
```

---

## 11. Uninstall

### Method A (Kustomize)

```bash
cd operator/
make undeploy    # removes operator Deployment, ClusterRole, etc.
make uninstall   # removes CRDs (and all CRs — destructive!)
```

### Method B (OLM bundle)

```bash
operator-sdk cleanup acc-operator --namespace acc-operator-system
```

### Method C (CatalogSource)

```bash
kubectl delete subscription acc-operator -n acc-operator-system
kubectl delete csv acc-operator.v0.1.0 -n acc-operator-system
kubectl delete catalogsource acc-operator-catalog -n openshift-marketplace
kubectl delete operatorgroup acc-operator-group -n acc-operator-system
# Remove CRDs (destructive — deletes all AgentCorpus and AgentCollective objects)
kubectl delete crd agentcorpora.acc.redhat.io agentcollectives.acc.redhat.io
```

> **PVC cleanup:** NATS and Redis PVCs are **not** deleted automatically when the operator is
> removed. Delete them manually to free storage:
> ```bash
> kubectl delete pvc -n acc-system -l app.kubernetes.io/managed-by=acc-operator
> ```
