# How to Run ACC in RHOAI Mode (OpenShift + Red Hat OpenShift AI)

RHOAI mode deploys the full ACC production stack on OpenShift 4.14+ with Red Hat OpenShift AI (RHOAI). It uses Milvus for vector storage, vLLM or Llama Stack for GPU-backed inference, NATS JetStream (3-node cluster), Redis Sentinel, OPA Gatekeeper ConstraintTemplates, KEDA-driven autoscaling, and an OpenTelemetry collector.

---

## Architecture

```
OpenShift 4.14+ Cluster
─────────────────────────────────────────────────────────────────────────────
  AgentCorpus CR ──► ACC Operator (controller-runtime)
                         │
         ┌───────────────┼───────────────────────────┐
         ▼               ▼                           ▼
   NATS (3-node)   Redis (Sentinel)         OPA Bundle Server
   JetStream       3 replicas              + Gatekeeper CTs
   cluster         AOF + RDB
         │
         ▼
   Agent Deployments × 5 roles
   (ingester · analyst · synthesizer · arbiter · observer)
         │
   KEDA ScaledObjects (optional)
         │
   KServe InferenceService (vLLM / Llama Stack)   ← RHOAI GPU nodes
         │
   Milvus (external, operator probes connectivity)
   Kafka (external, optional audit bridge)
─────────────────────────────────────────────────────────────────────────────
```

---

## Prerequisites

| Component | Minimum | Notes |
|-----------|---------|-------|
| OpenShift | 4.14+ | `oc` client required |
| RHOAI / ODH | 2.x | For GPU inference and KServe |
| Go | 1.22 | Operator build only |
| Container registry | quay.io or internal | For operator image |
| StorageClass | ReadWriteOnce | NATS and Redis PVCs |
| RAM per worker node | 16 GiB | For NATS + Redis + 5 agent pods |
| GPU nodes (optional) | Nvidia A100 / H100 | For vLLM InferenceService |

**Optional prerequisites** (detected at runtime — corpus degrades gracefully when absent):
- KEDA — for ScaledObjects autoscaling
- OPA Gatekeeper — for ConstraintTemplate enforcement
- Prometheus Operator — for PrometheusRule CRs
- Kafka — for audit bridge

---

## Step 1 — Build and Push the Operator Image

```bash
cd operator/

# Set your registry target
export IMG=quay.io/<your-org>/acc-operator:0.2.0

# Build the operator binary and image
podman build -f Containerfile -t $IMG .
podman push $IMG
```

---

## Step 2 — Install CRDs and Operator

### Method A — Kustomize (fastest for development)

```bash
# Install CRDs into the cluster
make install

# Deploy the operator
make deploy IMG=$IMG

# Verify operator pod is running
kubectl get pods -n acc-system
# NAME                                           READY   STATUS
# acc-operator-controller-manager-<hash>          2/2     Running
```

### Method B — OLM Bundle

```bash
# Build and push the bundle image
make bundle-build bundle-push BUNDLE_IMG=quay.io/<your-org>/acc-operator-bundle:0.2.0

# Create a CatalogSource pointing to the bundle
kubectl apply -f - <<EOF
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: acc-catalog
  namespace: openshift-marketplace
spec:
  sourceType: grpc
  image: quay.io/<your-org>/acc-operator-bundle:0.2.0
  displayName: "ACC Operator"
  publisher: "Red Hat AI Dev"
EOF

# Install via OperatorHub UI or:
kubectl apply -f operator/bundle/manifests/acc-operator.subscription.yaml
```

See [`docs/operator-install-local.md`](operator-install-local.md) for all three installation methods.

---

## Step 3 — Create the Namespace and Prerequisites

```bash
kubectl create namespace acc-system

# Category A WASM governance blob (required prerequisite)
# In production, build category_a.wasm from your Rego rules using the OPA build tool.
# For testing, use a placeholder:
kubectl create configmap acc-cat-a-wasm \
  --from-file=category_a.wasm=/path/to/category_a.wasm \
  -n acc-system
```

---

## Step 4 — Create the AgentCorpus CR

Save as `rhoai-corpus.yaml`:

```yaml
apiVersion: acc.redhat-ai-dev.io/v1alpha1
kind: AgentCorpus
metadata:
  name: my-corpus
  namespace: acc-system
spec:
  deployMode: rhoai                              # required
  version: "0.2.0"
  imageRegistry: registry.access.redhat.com

  infrastructure:
    # NATS JetStream: 3-node cluster for HA
    nats:
      replicas: 3
      version: "2.10"
      storageSize: "10Gi"
      storageClass: "ocs-storagecluster-ceph-rbd"   # adjust to your StorageClass

    # Redis: single node (set replicas: 3 for Sentinel HA)
    redis:
      replicas: 1
      version: "6"
      storageSize: "5Gi"

    # Milvus: external — operator probes connectivity, does not install Milvus
    milvus:
      uri: "http://milvus.milvus-system.svc.cluster.local:19530"
      collectionPrefix: "acc_"
      credentialsSecretRef:
        name: milvus-credentials     # Secret with milvus_user / milvus_password keys
        namespace: acc-system

  governance:
    categoryA:
      wasmConfigMapRef: "acc-cat-a-wasm"
    categoryB:
      pollIntervalSeconds: 30
      bundlePVCSize: "1Gi"
    categoryC:
      confidenceThreshold: "0.80"
      maxRulesPerCollective: 200
    gatekeeperIntegration: true       # sync Cat-A as ConstraintTemplates (if Gatekeeper installed)

  # Optional: Kafka audit bridge
  kafka:
    bootstrapServers: "kafka.kafka-system.svc.cluster.local:9092"
    auditTopic: "acc.audit.all"
    credentialsSecretRef:
      name: kafka-credentials
      namespace: acc-system

  observability:
    backend: otel
    otelCollector:
      endpoint: "https://otel-collector.monitoring.svc.cluster.local:4317"
      serviceName: "acc-agent"
      tlsInsecure: false
    prometheusRules: true
    grafanaDashboard: false

  collectives:
    - name: production-collective-01

  upgradePolicy:
    mode: auto
    requireApproval: true    # pause before upgrading NATS/Redis
```

```bash
kubectl apply -f rhoai-corpus.yaml

# Watch corpus come up
kubectl get agentcorpus my-corpus -n acc-system -w
# NAME        MODE    VERSION   PHASE
# my-corpus   rhoai   0.2.0     Provisioning → Ready (2-5 min)
```

---

## Step 5 — Create the AgentCollective CR

### vLLM Backend (RHOAI KServe InferenceService)

```yaml
apiVersion: acc.redhat-ai-dev.io/v1alpha1
kind: AgentCollective
metadata:
  name: production-collective-01
  namespace: acc-system
spec:
  collectiveId: "sol-prod-01"
  corpusRef:
    name: my-corpus
  heartbeatIntervalSeconds: 30

  llm:
    backend: vllm
    vllm:
      model: "meta-llama/Llama-3-70b-Instruct"
      resources:
        requests:
          nvidia.com/gpu: "1"
        limits:
          nvidia.com/gpu: "1"
    embeddingModel: "bge-large-en-v1.5"

  # KEDA autoscaling per role (requires KEDA installed)
  scaling:
    roleScaling:
      - role: analyst
        minReplicas: 1
        maxReplicas: 10
      - role: ingester
        minReplicas: 1
        maxReplicas: 5

  # Agent role definitions (mounted as ConfigMaps into each pod)
  agents:
    - role: ingester
      replicas: 2         # static baseline; KEDA scales above this
    - role: analyst
      replicas: 2
    - role: synthesizer
      replicas: 1
    - role: arbiter
      replicas: 1
    - role: observer
      replicas: 1

  # Role definition for this collective (see docs/howto-role-infusion.md)
  roleDefinition:
    purpose: "Process enterprise document ingestion and semantic analysis."
    persona: analytical
    taskTypes:
      - TASK_ASSIGN
      - SYNC_MEMORY
      - ANALYZE_SIGNAL
    allowedActions:
      - publish_signal
      - write_episode
      - read_vector_db
    categoryBOverrides:
      token_budget: 4096
      rate_limit_rpm: 120
    version: "1.0.0"
```

### Anthropic Backend

```yaml
  llm:
    backend: anthropic
    anthropic:
      model: "claude-sonnet-4-6"
      apiKeySecretRef:
        name: anthropic-credentials    # Secret with ACC_ANTHROPIC_API_KEY key
        key: ACC_ANTHROPIC_API_KEY
    embeddingModel: "all-MiniLM-L6-v2"
```

```bash
kubectl apply -f production-collective-01.yaml
```

---

## Step 6 — Verify the Deployment

### Check corpus status

```bash
kubectl get agentcorpus my-corpus -n acc-system -o yaml
```

Expected status:
```yaml
status:
  phase: Ready
  infrastructure:
    natsReady: true
    redisReady: true
    milvusConnected: true
    opaBundleReady: true
    otelCollectorReady: true
  prerequisites:
    allMet: true
    kedaInstalled: true
    gatekeeperInstalled: true
    rhoaiInstalled: true
    kserveInstalled: true
    prometheusRulesSupported: true
  collectiveStatuses:
    production-collective-01:
      phase: Ready
      readyAgents:
        ingester: 2
        analyst: 2
        synthesizer: 1
        arbiter: 1
        observer: 1
```

### Check agent pods

```bash
kubectl get pods -n acc-system -l acc.redhat.io/collective-id=sol-prod-01
# NAME                                    READY   STATUS    RESTARTS
# sol-prod-01-ingester-<hash>              1/1     Running   0
# sol-prod-01-analyst-<hash>              1/1     Running   0
# ...
```

### Watch agent heartbeats via NATS

```bash
# Port-forward NATS
kubectl port-forward -n acc-system svc/my-corpus-nats 4222:4222 &

# Subscribe to all subjects
nats sub "acc.sol-prod-01.>" --server nats://localhost:4222
```

### Check KEDA ScaledObjects

```bash
kubectl get scaledobjects -n acc-system
# NAME                         SCALETARGETKIND   MIN   MAX   READY
# sol-prod-01-analyst-scaleobj  Deployment       1     10    True
```

---

## RHOAI-Specific Notes

### Milvus Requirements

`deployMode: rhoai` requires both `vector_db.milvus_uri` and one of `llm.vllm_inference_url` or `llm.llama_stack_url` to be set (enforced by the Pydantic model validator). The operator does **not** install Milvus — it probes connectivity and emits a Warning event if Milvus is unreachable.

### KServe InferenceService

When `llm.backend: vllm`, the operator creates a `KServe InferenceService` in the `acc-system` namespace. The `ACC_VLLM_INFERENCE_URL` env var in agent pods is populated from the InferenceService status URL at runtime.

### Upgrade Approval

With `upgradePolicy.requireApproval: true`, the operator pauses before upgrading NATS and Redis (stateful components). To approve:

```bash
kubectl annotate agentcorpus my-corpus -n acc-system \
  acc.redhat.io/approve-upgrade=0.2.1
```

### OLM Automatic Upgrades

When installed via OLM, the operator subscription handles image-level upgrades. The `upgradePolicy.mode: auto` setting controls whether the operator automatically rolls agent Deployments after an upgrade or waits for annotation approval.

---

## Cross-Collective Bridge (Datacenter Hub)

In RHOAI mode, the datacenter corpus is the **hub** that edge nodes delegate to. No extra configuration is needed — the hub just needs to have an `AgentCollective` with the matching `collectiveId` that edge agents reference in `spec.edge.hubCollectiveId`.

The hub NATS cluster accepts leaf node connections on port 7422. Ensure your OpenShift Service exposes port 7422 externally (NodePort or LoadBalancer):

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-corpus-nats-leaf
  namespace: acc-system
spec:
  type: LoadBalancer
  selector:
    acc.redhat.io/component: nats
  ports:
    - name: leaf
      port: 7422
      targetPort: 7422
```

---

## See Also

- [`docs/operator-install-local.md`](operator-install-local.md) — Detailed operator installation (Kustomize, OLM, CatalogSource)
- [`docs/IMPLEMENTATION_SPEC_v0.2.0.md`](IMPLEMENTATION_SPEC_v0.2.0.md) — RHOAI integration design document
- [`docs/howto-role-infusion.md`](howto-role-infusion.md) — Configuring agent roles and behaviour
- [`docs/howto-edge.md`](howto-edge.md) — Connecting edge nodes to this datacenter hub
