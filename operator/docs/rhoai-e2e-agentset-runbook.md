# ACC on RHOAI — end-to-end runbook (operator → model → agentset)

This is a follow-along runbook for standing up ACC on a Red Hat OpenShift AI
(RHOAI) cluster end to end: install the operator (from the private catalog),
free a GPU, deploy a served model, and apply a complete **agentset** (an
`AgentCorpus` + its `AgentCollective`) that the operator reconciles into running
infrastructure + agents.

Every GUI step lists the **equivalent CLI** so you can drive it either way.
Screenshot placeholders (📸) mark where to capture the console for the visual
walkthrough. All YAML is inline so the whole flow can be reproduced manually.

Validated live on an RHOAI Single-Node OpenShift sandbox
(`api.ocp.b74q6.sandbox3207.opentlc.com`), 2026-06-08.

> Companion docs: `private-quay-to-ocp-catalog-deploy.md` (build → private Quay →
> OCP internal registry → catalog) and `WS-A-olm-bundle-runbook.md`.

---

## 0. Prerequisites on the cluster

| Bordering service | Why | Detect (CLI) |
|---|---|---|
| **RHOAI / OpenShift AI** | `deployMode=rhoai`, GPU model serving (KServe) | `oc get datasciencecluster -A` |
| **Kafka (AMQ Streams / Strimzi)** | preferred audit transport for large agent fleets | `oc get crd kafkas.kafka.strimzi.io` |
| **OPA Gatekeeper** | admission enforcement of Category-A rules | `oc get crd gatekeepers.operator.gatekeeper.sh` |
| **Prometheus / monitoring** | `observability.backend=otel` + PrometheusRules | `oc get crd prometheusrules.monitoring.coreos.com` |
| **GPU operator + a GPU node** | model serving | `oc get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}'` |

All five were present in the validated run.

---

## 1. Install the ACC operator (private catalog)

**GUI:** Administrator → Operators → Installed Operators → (after the
CatalogSource is created) find **Agentic Cell Corpus Operator** → Install into
`acc-system`.
📸 *OperatorHub tile + the install form.*

**CLI** (the catalog + subscription; images come from the OCP internal registry,
mirrored from the private Quay — see `private-quay-to-ocp-catalog-deploy.md`):

```bash
oc new-project acc-system 2>/dev/null || true
# marketplace pods pull the operator images from acc-system:
oc policy add-role-to-group system:image-puller system:serviceaccounts -n acc-system
oc apply -f operator/config/private-catalog/    # CatalogSource + OperatorGroup + Subscription

# verify
oc get catalogsource acc-catalog -n openshift-marketplace \
  -o jsonpath='{.status.connectionState.lastObservedState}'   # READY
oc -n acc-system get csv,deploy
# acc-operator.v0.1.0 Succeeded ; acc-operator-controller-manager 1/1
```

The OperatorGroup must be **single-namespace** (the CSV is OwnNamespace-only) and
the bundle ships **all four CRDs** (AgentCorpus, AgentCollective, AccCatalog,
AccPackageInstall) — both are baked into `operator/config/private-catalog/` and
the bundle. (Earlier failures here — see "Gotchas" — are fixed.)

---

## 2. Free a GPU for your model

A model won't schedule if the only GPU is already taken — the symptom in the
RHOAI **Models → Deployments** view is a **Failed** deployment with
`0/1 nodes are available: 1 Insufficient nvidia.com/gpu`.
📸 *The Failed deployment + the "Insufficient nvidia.com/gpu" tooltip.*

**Find what holds the GPU** (it may be a model in *another* project, invisible in
your filtered dashboard):

```bash
# node GPU capacity vs allocatable
oc get nodes -o jsonpath='{range .items[*]}{.metadata.name}: cap={.status.capacity.nvidia\.com/gpu} alloc={.status.allocatable.nvidia\.com/gpu}{"\n"}{end}'
# every pod requesting a GPU, across ALL namespaces
oc get pods -A -o json | jq -r '.items[] | select([.spec.containers[].resources.limits["nvidia.com/gpu"]] | any) | "\(.metadata.namespace)/\(.metadata.name) \(.status.phase)"'
# all InferenceServices, all projects
oc get inferenceservices -A
```

In the validated run a leftover tutorial model `my-first-model/llama-32-3b-instruct`
held the single GPU.

**Free it (reversible — stop, don't delete):**

**GUI:** RHOAI → Models → Deployments → (switch Project to the one that owns it)
→ ⋮ → **Stop**.
📸 *The ⋮ menu → Stop on the model holding the GPU.*

**CLI** (KServe stop annotation scales the predictor to 0, keeping the config):

```bash
oc annotate inferenceservice <name> -n <ns> serving.kserve.io/stop="true" --overwrite
# GPU is freed within ~30s; restart later by removing the annotation:
#   oc annotate inferenceservice <name> -n <ns> serving.kserve.io/stop- --overwrite
```

---

## 3. Deploy the model

**GUI:** RHOAI → Models → Deployments → **Deploy model** → pick the model +
**vLLM NVIDIA GPU ServingRuntime** + the `gpu-profile` hardware profile → Deploy.
📸 *Deploy-model form; then the deployment going Started → 3/3.*

**CLI** (the model is a KServe `InferenceService`; here the embedding model from
the screenshots):

```bash
oc get inferenceservice redhataiqwen3-embedding-8b -n acc-system \
  -o jsonpath='ready={.status.conditions[?(@.type=="Ready")].status} url={.status.url}{"\n"}'
# watch the predictor load (vLLM loads weights, then torch.compile — a few min for 8B)
oc logs -n acc-system -l serving.kserve.io/inferenceservice=redhataiqwen3-embedding-8b \
  -c kserve-container -f
```

Once the GPU is free the previously-`Pending` predictor schedules automatically
and loads the model.

> **Model-type note:** `Qwen3-Embedding-8B` is an **embedding** model. Use it for
> ACC's vector/memory embeddings. For agent **reasoning**, serve a **chat** model
> (e.g. a vLLM Llama/Mistral) and point the collective's LLM backend at that.

---

## 4. Apply a complete agentset

An agentset = an **`AgentCorpus`** (shared infra + governance) + one or more
**`AgentCollective`**s (the agents + their LLM backend). The collective below
consumes the **existing** RHOAI model via `vllm.deploy: false`.

**GUI:** Installed Operators → Agentic Cell Corpus Operator → **Create
AgentCorpus** (form-driven; `deployMode` is auto-defaulted to `rhoai` when a
DataScienceCluster is present, `observability` defaults to OTel + Grafana) →
then **Create AgentCollective**.
📸 *Create-AgentCorpus form (note deployMode auto = rhoai); Create-AgentCollective form.*

**CLI / manual YAML** — `operator/config/samples/acc_rhoai_e2e_agentset.yaml`:

```bash
oc apply -f operator/config/samples/acc_rhoai_e2e_agentset.yaml
```

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata: {name: acc-cat-a-wasm, namespace: acc-system}
data: {README: "placeholder for the Category-A governance WASM blob"}
---
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCorpus
metadata: {name: acc-e2e, namespace: acc-system}
spec:
  # deployMode omitted → webhook auto-detects RHOAI → "rhoai"
  collectives:
    - name: acc-e2e-collective
  infrastructure:
    milvus: {uri: "milvus.acc-system.svc:19530"}   # rhoai requires a Milvus URI
  governance:
    categoryA: {wasmConfigMapRef: acc-cat-a-wasm}
  # observability omitted → webhook defaults backend=otel (+ collector endpoint) + grafanaDashboard
---
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCollective
metadata: {name: acc-e2e-collective, namespace: acc-system}
spec:
  collectiveId: acc-e2e-collective
  corpusRef: {name: acc-e2e}
  llm:
    backend: vllm
    vllm:
      inferenceServiceRef: redhataiqwen3-embedding-8b   # consume the existing model
      model: RedHatAI/Qwen3-Embedding-8B
      deploy: false                                     # operator does NOT create the IS
  agents:
    - {role: ingester, replicas: 1}
    - {role: arbiter,  replicas: 1}
    - {role: observer, replicas: 1}
```

**Verify the operator reconciled the whole agentset:**

```bash
oc get agentcorpus acc-e2e -n acc-system -o jsonpath='deployMode={.spec.deployMode} phase={.status.phase}{"\n"}'
oc get agentcollective acc-e2e-collective -n acc-system -o jsonpath='phase={.status.phase}{"\n"}'
oc -n acc-system get statefulset,deploy | grep acc-e2e
# discovered in-cluster RHOAI models (operator scan, deployMode=rhoai):
oc get agentcorpus acc-e2e -n acc-system -o jsonpath='{.status.availableRHOAIModels}'
```

In the validated run the operator created, from the single agentset:
`acc-e2e-nats` + `acc-e2e-redis` (StatefulSets), `acc-e2e-opa-bundle` +
`acc-e2e-otel-collector` (Deployments), and **three agent Deployments**
`acc-e2e-collective-{ingester,arbiter,observer}`. `deployMode` auto-defaulted to
`rhoai`.
📸 *Topology/Workloads view in acc-system showing nats/redis/opa/otel + the 3 agents.*

### 4b. Consuming a model from another Data Science Project (cross-workspace)

A model often already runs in a *different* RHOAI workspace (namespace) than
the agentset. Point the collective at it with `inferenceServiceNamespace`:

```yaml
llm:
  backend: vllm
  vllm:
    inferenceServiceRef: llama-31-8b-instruct
    inferenceServiceNamespace: my-first-model   # the model's workspace
    model: llama-31-8b-instruct
    deploy: false
```

The operator reads the endpoint from the InferenceService status
(`status.address.url`, falling back to `status.url`) and injects it into every
agent pod as `ACC_VLLM_INFERENCE_URL`, which the runtime maps onto
`llm.vllm_inference_url`. Verify:

```bash
# the resolved endpoint as the agents see it:
oc -n acc-system get deploy acc-e2e-collective-observer \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="ACC_VLLM_INFERENCE_URL")].value}{"\n"}'
```

**Network caveat:** RHOAI/OpenShift namespaces may carry NetworkPolicies (or
service-mesh membership) that block cross-namespace traffic. If agents can't
reach the model even though the env var is set, allow ingress from the
agentset namespace to the model's predictor Service in the model's workspace.

---

## 5. Teardown

```bash
oc delete -f operator/config/samples/acc_rhoai_e2e_agentset.yaml
oc delete -f operator/config/private-catalog/
oc delete project acc-system
# restart the model you stopped:
oc annotate inferenceservice <name> -n <ns> serving.kserve.io/stop- --overwrite
```

---

## Gotchas hit + fixed during validation (so you don't)

1. **Bundle shipped 2 of 4 CRDs** → operator CrashLoopBackOff
   (`no matches for kind "AccCatalog"`). Fixed: all four CRDs in the bundle.
2. **PrometheusRules reconciler passed a nil Scheme** to `SetControllerReference`
   → panic on every reconcile on any cluster with Prometheus. Fixed.
3. **CSV omitted `apps/daemonsets` RBAC** (runtime-evidence) → reflector forbidden
   blocked cache sync → reconcile never completed. Fixed.
4. **OperatorGroup must be single-namespace** (CSV is OwnNamespace-only); an
   AllNamespaces group fails the CSV with `UnsupportedOperatorGroup`.
5. **Manager `runAsUser: 65532`** was rejected by `restricted-v2`; removed so
   OCP assigns a uid (the image is arbitrary-uid friendly).
6. **Leader-election needs `coordination.k8s.io/leases`** in the CSV; added.
7. **GPU contention**: a single GPU held by a model in another project blocks new
   deployments — stop the holder (§2).
