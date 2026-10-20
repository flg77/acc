# How to Run ACC in Edge Mode

Edge mode deploys ACC to a resource-constrained node (MicroShift, K3s, or Podman) that may have intermittent connectivity to a datacenter hub. The local collective operates autonomously on a fast intra-cell path when the hub is unreachable, and resumes hub synchronisation transparently when connectivity is restored.

---

## Architecture

```
Edge Node (MicroShift / K3s / Podman)              Datacenter Hub (OpenShift)
──────────────────────────────────────             ──────────────────────────
┌─────────────────────────────────┐                ┌──────────────────────────────┐
│ Local fast path (works OFFLINE) │                │  Hub NATS JetStream Cluster  │
│                                 │                │                              │
│  ingester → analyst → arbiter   │                │  acc.sol-dc-01.task          │
│  μs latency, LanceDB + Ollama   │                │  acc.sol-edge-01.heartbeat   │
│                                 │                │  acc.bridge.*.delegate       │
│ NATS JetStream (leaf node):     │                │                              │
│  acc.sol-edge-01.*  (local)     │                │  Hub analyst (70B model)     │
│  acc.bridge.*.* ────────────────►────────────────► processes delegated tasks   │
│  JetStream pending queue        │   port 7422    │  result returned via bridge  │
└─────────────────────────────────┘  (leaf node)   └──────────────────────────────┘
```

**Key properties:**
- Intra-collective subjects (`acc.sol-edge-01.*`) stay on the local NATS leaf node — they never flow to the hub unless explicitly configured.
- Bridge subjects (`acc.bridge.*`) flow through the leaf node connection to the hub.
- When the hub connection is down, pending bridge tasks queue in local JetStream and drain automatically on reconnect.
- Redis uses `maxmemory` + `allkeys-lru` eviction to prevent OOM on edge hardware.
- OTel Collector, PrometheusRules, KEDA ScaledObjects, and Gatekeeper ConstraintTemplates are **not deployed** in edge mode.

---

## Prerequisites

### Option A — MicroShift (Recommended for Production)

| Component | Minimum |
|-----------|---------|
| RHEL for Edge / Fedora IoT | RHEL 9.2+ |
| MicroShift | 4.14+ |
| RAM | 4 GB (8 GB with local Ollama) |
| Storage | NVMe SSD, 32 GB free (LanceDB + Redis + container images) |
| CPU | ARM64 or x86_64, 4 cores |
| Network | Not required for local operation; 4G/LTE or better for hub sync |

Install MicroShift: [Red Hat MicroShift documentation](https://access.redhat.com/documentation/en-us/red_hat_build_of_microshift).

### Option B — K3s

```bash
curl -sfL https://get.k3s.io | sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

### Option C — Podman (Development / Testing)

Use the standalone Podman setup with `deploy_mode: edge` in `acc-config.yaml`. The NATS leaf node configuration is applied via the rendered NATS config template — in Podman mode you manage `nats.conf` directly.

### Option D — Image mode (bootc): switch once, `bootc upgrade` per release

The edge profile's intended lifecycle. The node does not install a package;
it boots an image that carries the released `acc` RPM with the stack unit
enabled and `deploy_mode: edge` — built by `container/Containerfile.bootc`
from the public channel and published as
`quay.io/flg77/acc_images:acc-bootc-<version>`. Upgrades are atomic and keep
the previous image for a rollback, and a node that was offline simply picks
up the latest image when it next can.

```bash
# any bootc host (Fedora bootc, CentOS/RHEL image mode) becomes an ACC edge node:
sudo bootc switch quay.io/flg77/acc_images:acc-bootc-0.27.0
sudo systemctl reboot

# first boot: /etc persists across upgrades, so the node's own settings live there
sudo -e /etc/acc/acc.env                 # the secrets the image deliberately does not carry
sudo usermod -aG acc $USER               # /var/lib/acc is shared through group acc
sudo systemctl status acc-stack          # the stack pulls its container images on first start
acc-cli doctor                           # the `channel` row says image mode; --probe asks the registry

# every later release
sudo bootc upgrade                       # or `bootc upgrade --check` first
sudo bootc rollback                      # the previous image, if the new one misbehaves
```

Build the image yourself — the Fedora base by default, the EL line with the
CentOS bootc base, or from the lab mirror instead of COPR:

```bash
podman build -f container/Containerfile.bootc --build-arg ACC_VERSION=0.27.0 \
    -t quay.io/flg77/acc_images:acc-bootc-0.27.0 container
podman build -f container/Containerfile.bootc --build-arg ACC_VERSION=0.27.0 \
    --build-arg BASE_IMAGE=quay.io/centos-bootc/centos-bootc:stream10 --build-arg ACC_REPO_FAMILY=epel \
    -t quay.io/flg77/acc_images:acc-bootc-el10-0.27.0 container
podman build -f container/Containerfile.bootc --build-arg ACC_VERSION=0.27.0 \
    --build-arg ACC_REPO_URL=http://rpm.ic3net.internal:8080/acc-spearhead/ -t acc-bootc:lab container
```

`--build-arg ACC_BOUND_IMAGES=1` binds the stack's container images to the
host image (bootc *logically bound images*): `bootc upgrade` then pulls them
with the host image, so a node with no connectivity at stack start already
has them. Off by default, because every image the compose names must then
exist in the registry or the upgrade fails. `--build-arg ACC_PROFILE=edge`
applies the `edge` feature profile (`profiles/edge.yaml`) at build time.

A bootc host has no `dnf upgrade`: the image is the unit of change. To change
what the node runs, change the image and `bootc upgrade`.

### Optional — SPIFFE workload identity

Edge deployments can opt into SPIFFE workload identity for
`ROLE_UPDATE` authentication (`signing_mode: spiffe`).  Three
topologies are supported — `nested` (downstream of an rhoai
parent), `federated` (peer edge sites), and `ed25519` (the legacy
escape valve for constrained hardware).  Edge stays on `ed25519` by
default; SPIFFE is opt-in per site.  See
[`docs/spiffe-edge.md`](./spiffe-edge.md).

---

## Step 1 — Install the ACC Operator (MicroShift / K3s)

The operator lives in [`flg77/acc-operator`](https://github.com/flg77/acc-operator); its
install guide covers OperatorHub, the bundle image, your own catalog and a kustomize deploy.
The shortest path on an edge cluster is OLM plus the public bundle:

```bash
operator-sdk olm install --version 0.31.0       # once per cluster
kubectl create namespace acc-system
operator-sdk run bundle quay.io/flg77/acc-operator-bundle:0.3.0 -n acc-system --timeout 10m

# Verify the operator is running
kubectl get pods -n acc-system
# NAME                                 READY   STATUS    RESTARTS
# acc-operator-controller-manager-...  1/1     Running   0
```

> **Note:** OLM issues the admission webhooks' certificates. A kustomize deploy without OLM (`make install deploy` in the operator repository) needs a certificate source you provide; see the operator's `docs/install.md`.

---

## Step 2 — Create the AgentCorpus CR

Create a file `edge-corpus.yaml`:

```yaml
apiVersion: acc.redhat-ai-dev.io/v1alpha1
kind: AgentCorpus
metadata:
  name: edge-corpus
  namespace: acc-system
spec:
  deployMode: edge                           # required: selects edge profile
  version: "0.2.0"
  imageRegistry: registry.access.redhat.com

  # Edge-specific configuration
  edge:
    hubNatsUrl: "nats-leaf://hub.example.com:7422"   # hub leaf node URL
    hubCollectiveId: "sol-dc-01"                      # datacenter collective to delegate to
    hubRegistry: "registry.hub.example.com"           # image registry (pull on reconnect)
    redisMaxMemoryMb: 512                             # cap Redis at 512 MiB
    redisMaxMemoryPolicy: "allkeys-lru"               # evict LRU keys when limit reached

  # Infrastructure: single-node NATS (no clustering at edge)
  infrastructure:
    nats:
      replicas: 1
      version: "2.10"
      storageSize: "2Gi"
    redis:
      replicas: 1
      version: "6"
      storageSize: "2Gi"

  # Governance: WASM Cat-A (in-process); Cat-B pre-fetched from hub before potential disconnect
  governance:
    categoryA:
      wasmConfigMapRef: "acc-cat-a-wasm"
    categoryB:
      pollIntervalSeconds: 60               # less frequent polling at edge
    categoryC:
      confidenceThreshold: "0.85"           # higher threshold for edge arbiter

  # Collectives managed by this corpus
  collectives:
    - name: edge-collective-01

  # Observability: log only (no OTel Collector at edge)
  observability:
    backend: log                            # otel is NOT available in edge mode
```

Apply the corpus:
```bash
kubectl apply -f edge-corpus.yaml

# Watch it become Ready (may take 2-3 minutes for image pulls)
kubectl get agentcorpus edge-corpus -n acc-system -w
# NAME          MODE   VERSION   PHASE
# edge-corpus   edge   0.2.0     Ready
```

---

## Step 3 — Create the AgentCollective CR

```yaml
apiVersion: acc.redhat-ai-dev.io/v1alpha1
kind: AgentCollective
metadata:
  name: edge-collective-01
  namespace: acc-system
spec:
  collectiveId: "sol-edge-01"
  corpusRef:
    name: edge-corpus
  heartbeatIntervalSeconds: 30

  # LLM: Ollama default for edge (3B model, fits in 4 GB VRAM)
  llm:
    backend: ollama
    ollama:
      baseURL: "http://ollama-sidecar:11434"
      model: "llama3.2:3b"             # default applied by operator if empty
    embeddingModel: "all-MiniLM-L6-v2"  # CPU-friendly 384-dim model

  # Cross-collective bridge (ACC-9)
  # bridge_enabled and hub_collective_id come from EdgeSpec automatically;
  # the operator injects ACC_BRIDGE_ENABLED=true and ACC_HUB_COLLECTIVE_ID=sol-dc-01

  # Agent roles: static replicas (no KEDA at edge)
  agents:
    - role: ingester
      replicas: 1
    - role: analyst
      replicas: 1
    - role: arbiter
      replicas: 1
```

```bash
kubectl apply -f edge-collective-01.yaml
```

---

## Step 4 — Verify Edge Deployment

### Check corpus status

```bash
kubectl get agentcorpus edge-corpus -n acc-system -o yaml | grep -A 20 status:
```

Look for:
```yaml
status:
  phase: Ready
  infrastructure:
    natsReady: true
    natsLeafConnected: true    # true when leaf node has connected to hub
    redisReady: true
  prerequisites:
    allMet: true
    kedaInstalled: false       # expected — KEDA not present at edge
    gatekeeperInstalled: false # expected — Gatekeeper not present at edge
```

> **Note:** `NATSLeafConnected: false` is expected and non-fatal when `hubNatsUrl` is empty (disconnected operation). The corpus remains in `Ready` phase.

### Check NATS leaf connection

```bash
kubectl exec -n acc-system deploy/edge-corpus-nats -- \
  nats server info --server nats://localhost:4222 | grep -A 5 leafnodes
```

### Watch agent heartbeats

```bash
# Port-forward NATS for local observation
kubectl port-forward -n acc-system svc/edge-corpus-nats 4222:4222 &

# Subscribe to all collective subjects
nats sub "acc.sol-edge-01.>" --server nats://localhost:4222
```

---

## Step 5 — Configure Hub Connectivity

If the hub is reachable, the operator renders the NATS leaf node configuration automatically from `spec.edge.hubNatsUrl`. No manual NATS configuration is needed.

The generated `nats.conf` leafnodes block looks like:

```
leafnodes {
  remotes: [
    {
      url: "nats-leaf://hub.example.com:7422"
      deny_imports: [
        "acc.sol-edge-01.heartbeat",   # heartbeat stays local for privacy
        "acc.sol-edge-01.register",
      ]
    }
  ]
}
```

Local intra-collective subjects are kept local. Only bridge subjects (`acc.bridge.*`) and explicit imports from the hub flow through the leaf connection.

---

## Step 6 — Cross-Collective Bridge in Edge Mode

When `spec.edge.hubCollectiveId` is set, the operator automatically:
1. Sets `ACC_HUB_COLLECTIVE_ID=sol-dc-01` in agent pod environments.
2. Sets `ACC_BRIDGE_ENABLED=true` in agent pod environments.
3. Adds `sol-dc-01` to `ACC_PEER_COLLECTIVES`.

This means the LLM can emit delegation markers in its output:
```
[DELEGATE:sol-dc-01:task requires 70B model parameter window]
```

The agent will:
1. Publish the task to `acc.bridge.sol-edge-01.sol-dc-01.delegate`.
2. Wait up to 30 seconds for a result on `acc.bridge.sol-dc-01.sol-edge-01.result`.
3. If the hub is unreachable: queue the task in the JetStream `acc.bridge.sol-edge-01.pending` stream; the task is retried on reconnect.
4. If no result arrives within the timeout: handle the task locally with the available 3B model.

---

## Disconnected Operation

The following operations work **with no hub connectivity**:

| Operation | Offline? | Notes |
|---|---|---|
| Intra-collective task processing | Yes | ingester → analyst → arbiter pipeline |
| Local LLM inference (Ollama) | Yes | 3B model runs on-device |
| LanceDB episodic memory | Yes | Local NVMe storage |
| Cat-A governance (WASM) | Yes | In-process, no network required |
| Cat-B governance (OPA bundle) | Yes* | Uses last-fetched bundle; no hot-reload |
| Heartbeat accumulation | Yes | Stored in local JetStream; synced on reconnect |
| Bridge delegation | No | Queued locally; retried on reconnect |
| ROLE_UPDATE hot-reload | No | Applied when NATS leaf reconnects |
| Cat-C rule sync from hub | No | Holds last-known rules until reconnect |

\* Cat-B bundle cannot hot-reload during disconnection. Agents continue enforcing the last-fetched policy bundle.

---

## Differences from Standalone Mode

| Dimension | Standalone | Edge |
|---|---|---|
| `deploy_mode` | `standalone` | `edge` |
| NATS topology | Single node, no hub | Leaf node connecting to hub |
| Redis eviction | No eviction policy | `maxmemory 512mb` + `allkeys-lru` |
| KEDA ScaledObjects | Optional | Never deployed |
| Gatekeeper ConstraintTemplates | Optional | Never deployed |
| OTel Collector | Optional | Never deployed (metrics backend forced to `log`) |
| PrometheusRules | Optional | Never deployed |
| Bridge delegation | Optional | Automatically configured from `EdgeSpec` |
| LLM default model | Configurable | `llama3.2:3b` if model is empty |

---

## Operator Warning Events

The operator emits Kubernetes Warning events in edge mode for notable conditions:

| Event Reason | Message | Action |
|---|---|---|
| `EdgeHubUrlNotConfigured` | `spec.edge.hubNatsUrl is empty` | Set hub URL for bridge delegation; acceptable for fully air-gapped nodes |
| `KafkaUnreachable` | Bootstrap servers unreachable | Expected at edge if Kafka bridge is not configured |

KEDA, Gatekeeper, and RHOAI warnings are **suppressed in edge mode** — their absence is expected.

---

## See Also

- [`docs/howto-standalone.md`](howto-standalone.md) — Local Podman development setup
- [`docs/howto-rhoai.md`](howto-rhoai.md) — Datacenter hub setup (the other end of the bridge)
- [`docs/howto-role-infusion.md`](howto-role-infusion.md) — Configuring agent roles and behavior
