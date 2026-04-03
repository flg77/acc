# IMPLEMENTATION_SPEC v0.2.0 — ACC Integration with Red Hat AI Enterprise 3

**Version:** 0.2.0
**Status:** Draft — Evaluation Plan
**Date:** 2026-04-03
**Scope:** Integrate Agentic Cell Corpus (ACC) v0.1.0 into RHOAI 3 while preserving edge-first standalone capability
**Author:** Michael (via ACC Design Session)

---

## Table of Contents

1. [Executive Summary & Value Proposition](#1-executive-summary--value-proposition)
2. [Compatibility Matrix](#2-compatibility-matrix)
3. [Integration Architecture Diagram](#3-integration-architecture-diagram)
4. [Dual-Mode Design Pattern](#4-dual-mode-design-pattern)
5. [Signaling Layer: NATS JetStream to Kafka Bridge](#5-signaling-layer-nats-jetstream-to-kafka-bridge)
6. [Membrane / Governance Layer: OPA Alignment](#6-membrane--governance-layer-opa-alignment)
7. [Cognitive Core: vLLM + Llama Stack Coexistence](#7-cognitive-core-vllm--llama-stack-coexistence)
8. [Memory Layer: LanceDB (Edge) / Milvus (Datacenter)](#8-memory-layer-lancedb-edge--milvus-datacenter)
9. [Audit Log Pipeline](#9-audit-log-pipeline)
10. [Scaling Strategy: KEDA ScaledObjects for Agent Cells](#10-scaling-strategy-keda-scaledobjects-for-agent-cells)
11. [Cognitive Core Functional Diagram](#11-cognitive-core-functional-diagram)
12. [Regulatory Rule Templates (Categories A, B, C)](#12-regulatory-rule-templates-categories-a-b-c)
13. [Friction Points and Resolution Strategies](#13-friction-points-and-resolution-strategies)
14. [Levin Alignment Check](#14-levin-alignment-check)
15. [Implementation Sequencing and Dependencies](#15-implementation-sequencing-and-dependencies)
- [Appendix A: Answers to v2 Design Brief Questions](#appendix-a-answers-to-v2-design-brief-questions)
- [Appendix B: Updated Project Directory Structure](#appendix-b-updated-project-directory-structure)

---

## 1. Executive Summary & Value Proposition

### What ACC Adds Above RHOAI 3 Native Capabilities

RHOAI 3 provides production-grade ML infrastructure: model serving (KServe/vLLM), pipelines (Kubeflow), observability (OTel/Tempo), and Llama Stack for agent orchestration. However, it lacks a biologically-inspired governance layer for autonomous multi-agent collectives. ACC fills this gap:

| Capability | RHOAI 3 Native | ACC Adds |
|---|---|---|
| Agent orchestration | Llama Stack (request/response, tool use, RAG) | Persistent autonomous collectives with homeostatic self-regulation |
| Policy enforcement | OPA/Kyverno (cluster-level admission) | 3-tier governance (A/B/C) with learned adaptive rules per collective |
| Drift detection | None (manual monitoring) | Automated behavioral drift via embedding centroid divergence |
| Agent identity | None (stateless function calls) | Generational identity preserved across reprogramming events |
| Inter-agent memory | None | Episodic memory transfer via SYNC_MEMORY, ICL persistence |
| Rogue detection | Pod health checks (liveness/readiness) | Cognitive rogue detection (cancer analog) via signaling absence + drift |
| Reprogramming | Kill and replace pod | 5-level intervention ladder (Levin-derived), identity-preserving |

**Key insight:** Llama Stack treats agents as stateless tool-calling entities. ACC treats agents as persistent biological cells with memory, identity, and governance. These are complementary, not competing. ACC runs ABOVE Llama Stack -- it uses Llama Stack (or direct vLLM) as its cognitive backend while adding the governance, memory, and homeostatic layers.

### Levin Alignment Statement

Every integration decision in this document is evaluated against Michael Levin's core principles:
- **Competency at every scale:** Individual agents are competent; collectives emerge higher competency
- **Reprogramming over destruction:** Prefer setpoint adjustment to termination
- **Bioelectric signaling as cognitive glue:** Inter-agent signals bind cells into tissues
- **Goal-directedness is substrate-independent:** The governance model works regardless of LLM backend

---

## 2. Compatibility Matrix

### ACC Component x RHOAI 3 Component Alignment

| ACC Component | v0.1.0 Technology | RHOAI 3 Equivalent | Alignment | Integration Strategy |
|---|---|---|---|---|
| **Signaling transport** | NATS JetStream | Kafka (Red Hat Streams) + Service Mesh (Istio) | FRICTION | Keep NATS internal, bridge to Kafka for enterprise (Section 5) |
| **Rule enforcement** | OPA + Rego | OPA Gatekeeper / Kyverno | STRONG | Category A via admission webhooks; B/C via OPA bundle server (Section 6) |
| **Working memory** | Redis 7.4 | Redis (RHOAI ecosystem) | STRONG | Promote from sidecar to managed Redis cluster on RHOAI |
| **Vector/semantic memory** | LanceDB (embedded) | Milvus / pgvector / Elasticsearch | FRICTION | Edge=LanceDB, Datacenter=Milvus with sync bridge (Section 8) |
| **Wire serialization** | MessagePack | N/A (no opinion) | NEUTRAL | Unchanged; Kafka bridge translates to JSON for enterprise consumers |
| **LLM (edge)** | Ollama + llama3.2:3b | Not applicable on edge | NEUTRAL | Unchanged for edge-only deployments |
| **LLM (datacenter)** | Claude API (anthropic SDK) | KServe + vLLM, LLM-D distributed inference | FRICTION | Abstract via LLM backend interface; add vLLM backend (Section 7) |
| **Agent orchestration** | Custom cognitive core | Llama Stack | COMPLEMENTARY | ACC cognitive core wraps Llama Stack inference API (Section 7) |
| **Embedding model** | all-MiniLM-L6-v2 (local) | KServe ServingRuntime | ALIGNMENT | Deploy as KServe InferenceService on RHOAI; keep local for edge |
| **Container runtime** | Podman pods | OpenShift (CRI-O/Kubernetes) | STRONG | Same pod.yaml, different runtime (Section 4) |
| **Observability** | Custom HEARTBEAT/STATE signals | OpenTelemetry + Prometheus + Tempo + MLflow | COMPLEMENTARY | Map signals to OTel spans and Prometheus metrics (Section 9) |
| **Autoscaling** | Static (fixed agent count) | KEDA + HPA + VPA + scale-to-zero | COMPLEMENTARY | KEDA ScaledObjects for dynamic agent scaling (Section 10) |
| **MCP servers** | 3 custom MCP servers | Llama Stack MCP integration (developer preview) | COMPLEMENTARY | Register ACC MCP servers as Llama Stack tool providers |
| **Security** | Ed25519 signal signing | mTLS, RBAC, audit logging, FedRAMP/HIPAA | COMPLEMENTARY | Layer Ed25519 (agent-level) over mTLS (transport-level) |

---

## 3. Integration Architecture Diagram

```
+=====================================================================+
|                     RED HAT OPENSHIFT AI 3 CLUSTER                  |
|                                                                     |
|  +-------------------------------+  +----------------------------+  |
|  |   RHOAI Control Plane         |  |  Observability Stack       |  |
|  |  - OpenShift AI Dashboard     |  |  - OTel Collector          |  |
|  |  - Model Registry             |  |  - Prometheus              |  |
|  |  - Data Science Pipelines     |  |  - Tempo (traces)          |  |
|  |  - LlamaStackDistribution CRD |  |  - Grafana/Perses          |  |
|  +-------------------------------+  |  - MLflow Tracing          |  |
|                                     +----------------------------+  |
|  +---------------------------------------------------------------+  |
|  |                    ACC COLLECTIVE NAMESPACE                    |  |
|  |                                                                |  |
|  |  +------------------+  +------------------+  +--------------+  |  |
|  |  | Agent Pod:       |  | Agent Pod:       |  | Agent Pod:   |  |  |
|  |  | INGESTER         |  | ANALYST          |  | SYNTHESIZER  |  |  |
|  |  | +-------------+  |  | +-------------+  |  | +----------+ |  |  |
|  |  | |agent-core   |  |  | |agent-core   |  |  | |agent-core| |  |  |
|  |  | | cognitive    |  |  | | cognitive    |  |  | |cognitive | |  |  |
|  |  | | core         |--|--| | core         |--|--| |core      | |  |  |
|  |  | +------+------+  |  | +------+------+  |  | +----+-----+ |  |  |
|  |  |        |          |  |        |          |  |      |       |  |  |
|  |  | +------+------+  |  | +------+------+  |  | +----+-----+ |  |  |
|  |  | |opa-sidecar  |  |  | |opa-sidecar  |  |  | |opa-side  | |  |  |
|  |  | |(membrane)   |  |  | |(membrane)   |  |  | |(membrane)| |  |  |
|  |  | +-------------+  |  | +-------------+  |  | +----------+ |  |  |
|  |  +------------------+  +------------------+  +--------------+  |  |
|  |           |                     |                    |         |  |
|  |  +--------+---------------------+--------------------+------+  |  |
|  |  |              NATS JetStream (StatefulSet)                |  |  |
|  |  |    acc.{cid}.state | .alert | .task.* | .memory.sync     |  |  |
|  |  +---------------------------+------------------------------+  |  |
|  |                              |                                 |  |
|  |                    +---------+---------+                       |  |
|  |                    | NATS-Kafka Bridge |                       |  |
|  |                    | (Camel K / custom)|                       |  |
|  |                    +---------+---------+                       |  |
|  |                              |                                 |  |
|  +---------------------------------------------------------------+  |
|                                 |                                    |
|  +------------------------------+--------------------------------+  |
|  |              ENTERPRISE INTEGRATION LAYER                     |  |
|  |                                                                |  |
|  |  +------------------+  +------------------+ +--------------+  |  |
|  |  | Kafka (Red Hat   |  | KServe + vLLM    | | Milvus       |  |  |
|  |  | Streams)         |  | InferenceService | | (Vector DB)  |  |  |
|  |  | - audit topics   |  | - LLM serving    | | - collective |  |  |
|  |  | - event fanout   |  | - autoscaling    | |   memory     |  |  |
|  |  | - external APIs  |  | - GPU scheduling | | - patterns   |  |  |
|  |  +------------------+  +------------------+ +--------------+  |  |
|  |                                                                |  |
|  |  +------------------+  +------------------+ +--------------+  |  |
|  |  | Redis Cluster    |  | OPA Gatekeeper   | | Service Mesh |  |  |
|  |  | (managed)        |  | (Cat-A webhooks) | | (Istio)      |  |  |
|  |  | - agent state    |  | - admission ctrl | | - mTLS       |  |  |
|  |  | - task queues    |  | - policy audit   | | - Gateway API|  |  |
|  |  +------------------+  +------------------+ +--------------+  |  |
|  +---------------------------------------------------------------+  |
|                                                                     |
|  +------------------+     +--------------------------------------+  |
|  | ARBITER Pod      |     | KEDA ScaledObjects                   |  |
|  | - drift detection|---->| - scale agent pods by queue depth    |  |
|  | - reprogramming  |     | - scale-to-zero for idle collectives |  |
|  | - ICL consolid.  |     | - GPU-aware for vLLM pods            |  |
|  +------------------+     +--------------------------------------+  |
+=====================================================================+

         |                              |
    (disconnected                  (connected edge
     edge mode)                     to cluster)
         |                              |
+------------------+          +-------------------+
| EDGE: Podman Pod |          | EDGE: Podman Pod  |
| - agent-core     |          | - agent-core      |
| - redis-sidecar  |          | - redis-sidecar   |
| - opa-sidecar    |          | - opa-sidecar     |
| - Ollama (local) |          | - Ollama (local)  |
| - LanceDB (emb.) |          | - LanceDB -> sync |
| - NATS (embedded)|          |   to Milvus on    |
|                  |          |   reconnect        |
+------------------+          +-------------------+
```

---

## 4. Dual-Mode Design Pattern

### Principle: Same Codebase, Config-Driven Runtime

ACC must run identically in two modes from a single codebase. The mode is determined entirely by environment variables and config files -- no code branching.

### 4.1 Abstraction Interfaces

Each infrastructure-dependent component is accessed through an abstract interface. The concrete implementation is selected at startup by reading `ACC_DEPLOY_MODE` (values: `standalone` or `rhoai`).

```python
# acc/backends/__init__.py -- Backend registry pattern

from typing import Protocol, Callable

class SignalingBackend(Protocol):
    async def publish(self, subject: str, envelope: "SignalEnvelope") -> None: ...
    async def subscribe(self, subject: str, handler: Callable) -> None: ...

class VectorBackend(Protocol):
    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]: ...
    def insert(self, table: str, records: list[dict]) -> None: ...

class LLMBackend(Protocol):
    async def complete(self, system: str, user: str, schema: dict) -> dict: ...

class MetricsBackend(Protocol):
    def emit_span(self, name: str, attributes: dict) -> None: ...
    def emit_metric(self, name: str, value: float, labels: dict) -> None: ...
```

### 4.2 Backend Selection Matrix

| Interface | `standalone` mode | `rhoai` mode |
|---|---|---|
| `SignalingBackend` | `NATSBackend` (direct NATS JetStream) | `NATSBackend` (NATS inside cluster, bridge to Kafka) |
| `VectorBackend` | `LanceDBBackend` (embedded) | `MilvusBackend` (RHOAI-managed Milvus) |
| `LLMBackend` | `OllamaBackend` or `AnthropicBackend` | `VLLMBackend` (KServe InferenceService) or `LlamaStackBackend` |
| `MetricsBackend` | `LogMetricsBackend` (stdout JSON) | `OTelMetricsBackend` (OpenTelemetry SDK) |
| Redis | Sidecar (`redis://localhost:6379`) | Managed cluster (`redis://acc-redis-cluster:6379`) |
| OPA | Sidecar (`http://localhost:8181`) | Cluster-wide OPA + sidecar for Cat-C |

### 4.3 Environment Variable Additions for v0.2.0

| Variable | Required | Default | Description |
|---|---|---|---|
| `ACC_DEPLOY_MODE` | No | `standalone` | `standalone` or `rhoai` |
| `KAFKA_BOOTSTRAP_SERVERS` | Cond. | -- | Required if `ACC_DEPLOY_MODE=rhoai` and Kafka bridge enabled |
| `KAFKA_AUDIT_TOPIC` | No | `acc.audit` | Kafka topic for audit log events |
| `MILVUS_URI` | Cond. | -- | Required if `ACC_DEPLOY_MODE=rhoai` |
| `MILVUS_COLLECTION_PREFIX` | No | `acc_` | Prefix for Milvus collections |
| `VLLM_INFERENCE_URL` | Cond. | -- | KServe InferenceService URL for vLLM |
| `LLAMA_STACK_URL` | Cond. | -- | Llama Stack distribution endpoint |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Cond. | -- | OTel collector endpoint for RHOAI mode |
| `OTEL_SERVICE_NAME` | No | `acc-agent` | OTel service name |
| `KEDA_ENABLED` | No | `false` | Whether KEDA scaling is active |

### 4.4 Configuration File: acc-config.yaml

```yaml
# acc-config.yaml -- loaded at startup, overridden by env vars
deploy_mode: standalone  # or rhoai

signaling:
  backend: nats
  nats_url: nats://localhost:4222
  kafka_bridge:
    enabled: false
    bootstrap_servers: ""
    audit_topic: acc.audit
    signal_topics_prefix: acc.signals

vector_db:
  backend: lancedb  # or milvus
  lancedb_path: /data/lancedb
  milvus_uri: ""
  milvus_collection_prefix: acc_
  sync_on_reconnect: true  # LanceDB -> Milvus sync when edge reconnects

llm:
  backend: ollama  # ollama | anthropic | vllm | llama_stack
  ollama_base_url: http://localhost:11434
  vllm_inference_url: ""
  llama_stack_url: ""

observability:
  backend: log  # log | otel
  otel_endpoint: ""
  otel_service_name: acc-agent
  mlflow_tracking_uri: ""

governance:
  opa_url: http://localhost:8181
  category_a_wasm: /etc/acc/rules/category_a.wasm
  bundle_server_url: ""  # cluster-wide OPA bundle server in rhoai mode
```

---

## 5. Signaling Layer: NATS JetStream to Kafka Bridge

### 5.1 Why Keep NATS Internal

NATS JetStream remains the internal agent signaling bus even on RHOAI. Rationale:

1. **Latency:** NATS delivers sub-millisecond pub/sub; Kafka optimizes for throughput not latency. Agent HEARTBEAT at 30s intervals with sub-ms delivery is critical for rogue detection.
2. **Subject hierarchy:** NATS subject wildcards (`acc.{cid}.>`) map directly to ACC signal routing. Kafka topics are flat -- would require topic-per-signal-type or complex partitioning.
3. **Lightweight footprint:** NATS StatefulSet (3 pods, ~60MB RAM each) vs Kafka cluster (3+ brokers, ZooKeeper/KRaft, ~1GB+ RAM each).
4. **Edge parity:** Same NATS client code runs on Podman edge and OpenShift datacenter. Kafka has no edge story.

### 5.2 NATS-to-Kafka Bridge Architecture

The bridge runs as a dedicated pod in the ACC namespace. It subscribes to NATS subjects and publishes translated messages to Kafka topics for enterprise consumption.

```
+-------------------+       +----------------------+       +------------------+
| NATS JetStream    |       | NATS-Kafka Bridge    |       | Kafka (Red Hat   |
| (internal bus)    |------>| (Camel K or custom   |------>| Streams)         |
|                   |       |  Python service)     |       |                  |
| Subjects:         |       | Transforms:          |       | Topics:          |
| acc.{cid}.state   |       | - MessagePack->JSON  |       | acc.audit.all    |
| acc.{cid}.alert   |       | - Add OTel trace ctx |       | acc.signals.state|
| acc.{cid}.task.*  |       | - Filter by config   |       | acc.signals.alert|
| acc.{cid}.drift   |       | - Enrich with k8s    |       | acc.events.task  |
| acc.{cid}.health  |       |   metadata (ns, pod) |       | acc.events.drift |
+-------------------+       +----------------------+       +------------------+
                                      |
                                      v
                             +------------------+
                             | OTel Collector   |
                             | (sidecar in      |
                             |  bridge pod)     |
                             +------------------+
```

### 5.3 Bridge Implementation Options

**Option A: Apache Camel K (Recommended for RHOAI)**
- Red Hat-supported integration framework already in RHOAI ecosystem
- `KameletBinding` CRD: NATS source -> transformation -> Kafka sink
- Declarative YAML, no custom code for simple routing
- Limitation: custom MessagePack deserialization requires a custom processor

**Option B: Custom Python Bridge Service**
- Full control over MessagePack deserialization, OTel context injection
- Reuses ACC's existing `signaling.py` NATS client code
- Deploy as a standard Deployment in the ACC namespace
- More flexible but more maintenance burden

**Recommendation:** Start with Option B (custom Python bridge) for v0.2.0 because MessagePack deserialization and OTel trace context injection require custom logic. Evaluate migration to Camel K in v0.3.0 once the message format stabilizes.

### 5.4 Bridge Kafka Topic Schema

```yaml
# Kafka topics created by the bridge
acc.audit.all:
  partitions: 6
  replication_factor: 3
  retention_ms: 2592000000  # 30 days
  # All signals forwarded here for audit/compliance
  # Key: collective_id
  # Value: JSON-encoded SignalEnvelope (MessagePack decoded)

acc.signals.alert:
  partitions: 3
  replication_factor: 3
  retention_ms: 604800000  # 7 days
  # ALERT_ESCALATE signals only -- for external alerting integrations

acc.events.task:
  partitions: 6
  replication_factor: 3
  retention_ms: 604800000
  # TASK_ASSIGN + TASK_COMPLETE -- for pipeline/workflow integration

acc.metrics.drift:
  partitions: 3
  replication_factor: 3
  retention_ms: 86400000  # 1 day
  # DRIFT_DETECTED -- for dashboarding
```

### 5.5 Signal Router and Service Mesh Integration

The Tools MCP (Signal Router) from v0.1.0 exposes `publish_signal()` and `query_collective()` to agent cognitive cores. On RHOAI, this integrates with Service Mesh:

- **Istio ambient mode mTLS:** All NATS traffic between agent pods is automatically encrypted by the mesh. This is BELOW the Ed25519 signal signing -- mTLS secures transport, Ed25519 authenticates agent identity.
- **Gateway API Inference Extensions:** External clients (dashboards, human operators) access agent state via Gateway API routes that proxy to the Tools MCP HTTP endpoint. Rate limiting and auth handled by the gateway.
- **Traffic policy:** Istio `AuthorizationPolicy` restricts which pods can connect to NATS. Only pods with label `app: acc` in the collective namespace.

---

## 6. Membrane / Governance Layer: OPA Alignment

### 6.1 Strong Alignment with RHOAI

OPA is already part of RHOAI 3's enterprise policy stack (OPA Gatekeeper for admission control, Kyverno as alternative). ACC's use of OPA for agent-level governance aligns perfectly. The integration strategy layers ACC governance on top of cluster governance:

```
+------------------------------------------------------------------+
|  CLUSTER LEVEL (OPA Gatekeeper / Kyverno)                        |
|  - Kubernetes admission webhooks                                  |
|  - Pod security standards                                         |
|  - Resource quotas enforcement                                    |
|  - Network policy validation                                      |
+------------------------------------------------------------------+
        |
        v
+------------------------------------------------------------------+
|  NAMESPACE LEVEL (ACC Category A -- via Gatekeeper Constraints)   |
|  - A-001: Pods must carry collective_id label                     |
|  - A-002: Only arbiter-role pods can publish to .isolation subject |
|  - A-003: Agent pods must mount OPA bundle volume                 |
|  - A-004: Ed25519 key Secret must exist before pod creation       |
+------------------------------------------------------------------+
        |
        v
+------------------------------------------------------------------+
|  AGENT LEVEL (ACC Categories A/B/C -- OPA sidecar per pod)       |
|  - A-*: Constitutional rules (WASM in membrane binary)            |
|  - B-*: Conditional setpoints (OPA bundle server, live-updatable) |
|  - C-*: Adaptive learned rules (arbiter-signed bundles)           |
+------------------------------------------------------------------+
```

### 6.2 Category A: Admission Webhook Enforcement

On RHOAI, Category A constitutional rules gain a second enforcement point via OPA Gatekeeper ConstraintTemplates. This provides defense-in-depth -- even if the agent membrane is compromised, the cluster rejects invalid configurations.

```yaml
# deploy/rhoai/gatekeeper/constraint-template-collective-label.yaml
apiVersion: templates.gatekeeper.sh/v1
kind: ConstraintTemplate
metadata:
  name: acccollectivelabel
spec:
  crd:
    spec:
      names:
        kind: ACCCollectiveLabel
  targets:
    - target: admission.k8s.gatekeeper.sh
      rego: |
        package acc.admission.collective_label
        violation[{"msg": msg}] {
          input.review.object.kind == "Pod"
          labels := input.review.object.metadata.labels
          not labels["acc.collective-id"]
          msg := "ACC agent pods must have acc.collective-id label"
        }
```

### 6.3 Category B/C: OPA Bundle Server on RHOAI

In standalone mode, each agent runs an OPA sidecar loading bundles from a local ConfigMap. On RHOAI, a centralized OPA bundle server runs as a Deployment, serving bundles to all agent OPA sidecars via HTTP polling. The arbiter pushes updated bundles (Cat B setpoint changes, Cat C learned rules) to this server.

```
Arbiter Pod                    OPA Bundle Server (Deployment)
  |                                    |
  |-- signs Cat C bundle ------------->|-- serves bundles via HTTP -->  Agent OPA sidecars
  |-- updates Cat B setpoints -------->|                                (poll every 30s)
  |                                    |
  |                           PVC: /bundles/
  |                             category_a/ (immutable, mounted RO)
  |                             category_b/ (arbiter-writable)
  |                             category_c/ (arbiter-writable)
```

### 6.4 Compliance Mapping

RHOAI 3 targets FedRAMP, HIPAA, and PCI compliance. ACC's governance layer directly supports these:

| Compliance Requirement | ACC Feature | RHOAI Integration |
|---|---|---|
| Audit trail of all decisions | Signal audit log via NATS -> Kafka | Kafka retention + Elasticsearch indexing |
| Immutable security policies | Category A rules (WASM, no runtime update) | Gatekeeper ConstraintTemplates (cluster-level) |
| Access control | Ed25519 agent identity + role-based signal filtering | RBAC + mTLS + ServiceAccount per agent role |
| Anomaly detection | Drift detection via embedding centroid | Prometheus alerts + Grafana dashboards |
| Incident response | Reprogramming ladder + isolation protocol | AlertManager integration + PagerDuty/Slack webhooks |

---

## 7. Cognitive Core: vLLM + Llama Stack Coexistence

### 7.1 The Layering Model

ACC's cognitive core is NOT replaced by Llama Stack. They operate at different abstraction levels:

```
+------------------------------------------------------------------+
|  ACC COGNITIVE CORE (acc/cognitive_core.py)                       |
|  - 3-tier governance loop (evaluate rules before/after LLM call) |
|  - ICL persistence (episodes -> patterns -> Category C rules)    |
|  - Pattern recognition (embedding similarity, drift detection)   |
|  - Structured reasoning template (thinking/decision/output)      |
|  - Homeostatic feedback (health_score, generation tracking)      |
+------------------------------------------------------------------+
        |  calls down to
        v
+------------------------------------------------------------------+
|  LLM BACKEND INTERFACE (acc/backends/llm.py)                     |
|  Implementations:                                                 |
|  - OllamaBackend    -> Ollama REST API (edge)                    |
|  - AnthropicBackend -> Claude API (datacenter, direct)           |
|  - VLLMBackend      -> KServe InferenceService (RHOAI)           |
|  - LlamaStackBackend -> Llama Stack inference API (RHOAI)        |
+------------------------------------------------------------------+
        |  calls down to
        v
+------------------------------------------------------------------+
|  MODEL SERVING (infrastructure)                                   |
|  - Ollama process (edge)                                          |
|  - KServe + vLLM (RHOAI, GPU-scheduled, autoscaled)              |
|  - LLM-D distributed inference (RHOAI, multi-node)               |
+------------------------------------------------------------------+
```

### 7.2 vLLM Backend via KServe InferenceService

On RHOAI, the cognitive core uses vLLM served via KServe InferenceService. The `VLLMBackend` sends requests to the OpenAI-compatible API endpoint that KServe exposes.

```yaml
# deploy/rhoai/kserve/inference-service-llm.yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: acc-llm
  namespace: acc-collective
  annotations:
    serving.kserve.io/deploymentMode: RawDeployment
spec:
  predictor:
    model:
      modelFormat:
        name: vLLM
      runtime: vllm-runtime
      storageUri: pvc://acc-models/llama-3.2-3b
      resources:
        requests:
          cpu: "4"
          memory: "8Gi"
          nvidia.com/gpu: "1"
        limits:
          nvidia.com/gpu: "1"
```

### 7.3 Llama Stack Backend Implementation

When using Llama Stack as the LLM backend, ACC's cognitive core calls the Llama Stack inference API. This provides access to Llama Stack's built-in safety guardrails, RAG capabilities, and telemetry -- all of which complement ACC's own governance.

```python
# acc/backends/llm_llama_stack.py -- conceptual implementation
class LlamaStackBackend(LLMBackend):
    """
    Calls Llama Stack's /inference/chat-completion endpoint.
    Llama Stack adds: safety shields, RAG retrieval, MLflow tracing.
    ACC adds: 3-tier governance, ICL persistence, drift detection.
    """
    def __init__(self, llama_stack_url: str):
        self.url = llama_stack_url  # LlamaStackDistribution CRD endpoint

    async def complete(self, system: str, user: str, schema: dict) -> dict:
        # ACC cognitive core has already:
        #   1. Loaded Category A/B/C rules into the system prompt
        #   2. Retrieved top-k episodes from LanceDB for ICL priming
        #   3. Computed current agent state for context
        # Llama Stack adds safety shields on top
        response = await self._post("/inference/chat-completion", {
            "model_id": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "response_format": {"type": "json_schema", "json_schema": schema}
        })
        return response["completion_message"]["content"]
```

### 7.4 Coexistence Summary

| Feature | Llama Stack Provides | ACC Cognitive Core Provides |
|---|---|---|
| LLM inference | Yes (via vLLM/TGI backend) | Calls through to Llama Stack |
| Safety guardrails | Llama Guard shields | Category A constitutional rules |
| Tool calling | MCP tool integration | 3 MCP servers (Skills, Resources, Signal Router) |
| RAG | Built-in retrieval | ICL episode retrieval from LanceDB/Milvus |
| Telemetry | MLflow Tracing | OTel spans for governance decisions + signal audit |
| Agent memory | None (stateless) | Redis working state + LanceDB episodic memory |
| Multi-agent coordination | None | NATS signaling, arbiter, collective governance |
| Drift detection | None | Embedding centroid divergence |
| Adaptive learning | None | ICL -> Pattern -> Category C rule generation |

---

## 8. Memory Layer: LanceDB (Edge) / Milvus (Datacenter)

### 8.1 Dual Vector DB Strategy

| Environment | Vector DB | Rationale |
|---|---|---|
| Edge (Podman) | LanceDB (embedded) | Zero infrastructure, runs in-process, PyArrow schemas, no GPU |
| RHOAI datacenter | Milvus (RHOAI-native) | Managed, scalable, shared across agents, backed by S3/PVC |

### 8.2 Schema Mapping: LanceDB to Milvus

LanceDB uses PyArrow schemas (defined in v0.1.0 Section 7.2). Milvus uses collection schemas. The mapping is direct:

```
LanceDB Table: episodes          ->  Milvus Collection: acc_episodes
  record_id    (pa.string)            record_id    (VARCHAR, primary key)
  agent_id     (pa.string)            agent_id     (VARCHAR)
  collective_id(pa.string)            collective_id(VARCHAR)
  task_id      (pa.string)            task_id      (VARCHAR)
  task_type    (pa.string)            task_type    (VARCHAR)
  outcome      (pa.string)            outcome      (VARCHAR)
  embedding    (pa.list_(f32, 384))   embedding    (FLOAT_VECTOR, dim=384)
  icl_note     (pa.string)            icl_note     (VARCHAR)
  created_at_ms(pa.int64)             created_at_ms(INT64)
  metadata     (pa.string)            metadata     (VARCHAR)

LanceDB Table: patterns         ->  Milvus Collection: acc_patterns
  (same field mapping pattern)

LanceDB Table: collective_mem   ->  Milvus Collection: acc_collective_mem
  (same field mapping pattern)
```

### 8.3 Edge-to-Datacenter Sync Protocol

When an edge agent reconnects to the RHOAI cluster, its local LanceDB data syncs to Milvus:

```
1. Agent detects RHOAI connectivity (NATS cluster reachable)
2. Agent reads all LanceDB records with created_at_ms > last_sync_ms
3. Agent publishes SYNC_MEMORY signals to NATS (batched, 50 records per signal)
4. Milvus sync worker (Deployment) subscribes to acc.{cid}.memory.sync
5. Worker deduplicates by record_id against Milvus
6. Worker inserts new records into Milvus collections
7. Worker publishes TASK_COMPLETE acknowledgment
8. Agent updates last_sync_ms in Redis
```

**Conflict resolution:** Last-write-wins by `created_at_ms`. No vector merging -- records are immutable once written. If the same `record_id` exists with different timestamps, the newer record wins.

### 8.4 Redis: Sidecar to Managed Cluster

On RHOAI, Redis transitions from per-agent sidecar to a shared managed Redis cluster:

| Aspect | Standalone (sidecar) | RHOAI (managed cluster) |
|---|---|---|
| Deployment | Container in agent pod | Redis Operator / managed service |
| Persistence | `emptyDir` or small PVC | Replicated PVCs with backup |
| Key isolation | Single-tenant (one agent) | Multi-tenant; keys already namespaced by `acc:agent:{id}` |
| High availability | None (single instance) | Redis Sentinel or Cluster mode |
| Connection | `redis://localhost:6379` | `redis://acc-redis-cluster.acc-collective:6379` |

The existing Redis key schema from v0.1.0 (Section 7.1) is already namespaced by agent_id and collective_id, so no schema changes are needed for multi-tenancy.

---

## 9. Audit Log Pipeline

### 9.1 Pipeline Architecture

```
Agent Cognitive Core
  |
  |-- (1) LLM call with structured prompt
  |       |
  |       +-- OTel span: acc.cognitive.llm_call
  |           attributes: agent_id, task_id, model, token_count
  |
  |-- (2) Governance decision (OPA evaluation)
  |       |
  |       +-- OTel span: acc.governance.opa_eval
  |           attributes: rule_category, rule_id, decision (allow/deny)
  |
  |-- (3) Signal publish (via membrane)
  |       |
  |       +-- OTel span: acc.signal.publish
  |           attributes: signal_type, from_agent, collective_id
  |
  v
NATS JetStream
  |
  |-- All signals persisted in JetStream stream (retention: 7 days)
  |
  v
NATS-Kafka Bridge (Section 5)
  |
  |-- Translates MessagePack -> JSON
  |-- Injects OTel trace context (W3C traceparent header)
  |-- Publishes to Kafka topics
  |
  v
+------------------+     +------------------+     +------------------+
| Kafka            |     | OTel Collector   |     | Elasticsearch    |
| acc.audit.all    |---->| (RHOAI managed)  |---->| (long-term index)|
| acc.signals.*    |     |                  |     |                  |
+------------------+     +--+-----------+---+     +------------------+
                            |           |
                            v           v
                    +----------+  +-----------+
                    | Tempo    |  | Prometheus |
                    | (traces) |  | (metrics)  |
                    +----------+  +-----------+
                            |           |
                            v           v
                    +---------------------------+
                    | Grafana / Perses Dashboard |
                    | - Agent health heatmap     |
                    | - Drift score timeseries   |
                    | - Signal flow visualization|
                    | - Governance decision audit |
                    +---------------------------+
```

### 9.2 OTel Span Mapping for ACC Signals

| ACC Signal Type | OTel Span Name | Key Attributes | Prometheus Metric |
|---|---|---|---|
| HEARTBEAT | `acc.agent.heartbeat` | agent_id, health_score, state | `acc_agent_health_score` (gauge) |
| STATE_BROADCAST | `acc.agent.state_broadcast` | agent_id, state, drift_score | `acc_agent_drift_score` (gauge) |
| ALERT_ESCALATE | `acc.agent.alert` | agent_id, alert_code, severity | `acc_alerts_total` (counter, label: severity) |
| TASK_ASSIGN | `acc.task.assign` | task_id, task_type, assigned_to | `acc_tasks_assigned_total` (counter) |
| TASK_COMPLETE | `acc.task.complete` | task_id, outcome, duration_ms | `acc_task_duration_ms` (histogram) |
| SYNC_MEMORY | `acc.memory.sync` | from_agent, record_count, sync_type | `acc_memory_syncs_total` (counter) |
| RULE_UPDATE | `acc.governance.rule_update` | category, bundle_hash | `acc_rule_updates_total` (counter, label: category) |
| DRIFT_DETECTED | `acc.governance.drift` | agent_id, drift_score, threshold | `acc_drift_events_total` (counter) |
| ISOLATION | `acc.governance.isolation` | target_agent, reason | `acc_isolations_total` (counter) |

### 9.3 MLflow Tracing for Cognitive Core

On RHOAI, every LLM call from the cognitive core is traced via MLflow Tracing (integrated with Llama Stack). This provides:
- Token usage tracking per agent per task
- Latency percentiles (p50, p95, p99) for cognitive decisions
- Input/output logging for debugging governance decisions
- Cost attribution when using cloud LLM backends

```python
# Integration point in acc/cognitive_core.py
import mlflow

@mlflow.trace(name="acc.cognitive_core.reason")
async def reason(self, task: dict, episodes: list[dict]) -> dict:
    # ... existing cognitive core logic ...
    # MLflow automatically captures input/output/latency
    pass
```

### 9.4 Prometheus AlertManager Rules for ACC

```yaml
# deploy/rhoai/prometheus/acc-alert-rules.yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: acc-collective-alerts
  namespace: acc-collective
spec:
  groups:
    - name: acc.agent.health
      rules:
        - alert: ACCAgentDegraded
          expr: acc_agent_health_score < 0.5
          for: 2m
          labels:
            severity: warning
          annotations:
            summary: "ACC agent {{ $labels.agent_id }} health below 0.5"

        - alert: ACCAgentDriftExceeded
          expr: acc_agent_drift_score > 0.35
          for: 1m
          labels:
            severity: warning
          annotations:
            summary: "ACC agent {{ $labels.agent_id }} drift score {{ $value }} exceeds threshold"

        - alert: ACCAgentHeartbeatMissing
          expr: time() - acc_agent_last_heartbeat_timestamp > 60
          for: 30s
          labels:
            severity: critical
          annotations:
            summary: "ACC agent {{ $labels.agent_id }} missed heartbeat for >60s (rogue candidate)"

        - alert: ACCCollectiveIsolationEvent
          expr: increase(acc_isolations_total[5m]) > 0
          labels:
            severity: critical
          annotations:
            summary: "Agent isolation event in collective {{ $labels.collective_id }}"
```

---

## 10. Scaling Strategy: KEDA ScaledObjects for Agent Cells

### 10.1 Scaling Model Overview

ACC has two levels of scaling that must coordinate:

| Level | Controller | What It Scales | Trigger |
|---|---|---|---|
| **Logical** | Arbiter (ACC) | Task assignment, cognitive load balancing | Task queue depth, agent health scores |
| **Physical** | KEDA / HPA (RHOAI) | Pod count (agent instances) | NATS/Kafka queue depth, CPU/memory, custom metrics |

The arbiter manages WHICH agents get tasks (logical). KEDA manages HOW MANY agent pods exist (physical). They must not conflict.

### 10.2 KEDA ScaledObject for Agent Roles

Each agent role (ingester, analyst, synthesizer) gets its own KEDA ScaledObject. The arbiter is NOT scaled by KEDA -- there is always exactly one arbiter per collective.

```yaml
# deploy/rhoai/keda/scaledobject-ingester.yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: acc-ingester-scaler
  namespace: acc-collective
spec:
  scaleTargetRef:
    name: acc-ingester  # Deployment name
  pollingInterval: 15
  cooldownPeriod: 120
  minReplicaCount: 0    # Scale to zero when idle
  maxReplicaCount: 10
  triggers:
    # Scale based on NATS JetStream consumer lag
    - type: nats-jetstream
      metadata:
        natsServerMonitoringEndpoint: "acc-nats.acc-collective:8222"
        account: "$G"
        stream: "ACC_TASKS"
        consumer: "ingester-group"
        lagThreshold: "5"   # Scale up when >5 pending tasks

    # Also scale on CPU utilization as a safety valve
    - type: cpu
      metricType: Utilization
      metadata:
        value: "70"
```

```yaml
# deploy/rhoai/keda/scaledobject-analyst.yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: acc-analyst-scaler
  namespace: acc-collective
spec:
  scaleTargetRef:
    name: acc-analyst
  pollingInterval: 15
  cooldownPeriod: 180  # Longer cooldown -- analysts have state
  minReplicaCount: 0
  maxReplicaCount: 5
  triggers:
    - type: nats-jetstream
      metadata:
        natsServerMonitoringEndpoint: "acc-nats.acc-collective:8222"
        account: "$G"
        stream: "ACC_TASKS"
        consumer: "analyst-group"
        lagThreshold: "3"

    # Scale based on Prometheus metric from ACC drift detection
    - type: prometheus
      metadata:
        serverAddress: "http://prometheus.openshift-monitoring:9090"
        query: |
          avg(acc_agent_health_score{role="analyst"})
        threshold: "0.6"  # Scale up when average health drops below 0.6
        activationThreshold: "0.5"
```

### 10.3 Scale-to-Zero for Idle Collectives

When a collective has no pending tasks, KEDA scales all agent pods (except arbiter) to zero. The arbiter remains at 1 replica to accept incoming task assignments and scale agents back up.

```yaml
# deploy/rhoai/keda/scaledobject-arbiter.yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: acc-arbiter-scaler
  namespace: acc-collective
spec:
  scaleTargetRef:
    name: acc-arbiter
  minReplicaCount: 1    # NEVER scale to zero -- arbiter is always on
  maxReplicaCount: 1    # Exactly one arbiter per collective
  triggers:
    - type: cpu
      metricType: Utilization
      metadata:
        value: "80"     # Only trigger is CPU -- arbiter does not scale horizontally
```

### 10.4 Scaling Coordination Protocol

When KEDA scales up a new agent pod:

1. New pod starts, agent enters REGISTERING state
2. Agent publishes identity to `acc.{cid}.registry`
3. Arbiter ACKs registration, agent moves to IDLE
4. Arbiter can now assign tasks to the new agent
5. Agent inherits collective's current Category B/C rules from OPA bundle server

When KEDA scales down (removing an agent pod):

1. Kubernetes sends SIGTERM to the pod
2. Agent catches SIGTERM, publishes final STATE_BROADCAST with state=TERMINATED
3. Agent flushes pending LanceDB writes and Redis state
4. Arbiter detects agent departure via missing heartbeat (graceful) or TERMINATED state
5. Arbiter redistributes any in-flight tasks from the terminated agent

**Levin alignment:** Scale-down is analogous to apoptosis -- programmed cell death with orderly cleanup and memory preservation. The agent's episodic memory persists in LanceDB/Milvus and is available to future agents.

### 10.5 GPU-Aware Scaling for vLLM

When the cognitive core uses vLLM on RHOAI, GPU resources constrain scaling:

```yaml
# KEDA trigger using vLLM queue depth metric
- type: prometheus
  metadata:
    serverAddress: "http://prometheus.openshift-monitoring:9090"
    query: |
      vllm:num_requests_waiting{model_name="acc-llm"}
    threshold: "10"  # Scale LLM InferenceService when queue > 10
```

The agent pods themselves do NOT require GPUs -- they are CPU-only pods that call the shared vLLM InferenceService over HTTP. Only the KServe InferenceService pod needs GPU allocation. This means KEDA can scale agent pods freely without GPU constraints.

---

## 11. Cognitive Core Functional Diagram

### 11.1 Full Functional Diagram

```
+=========================================================================+
|                        COGNITIVE CORE                                    |
|                   (acc/cognitive_core.py)                                |
|                                                                          |
|  INBOUND SIGNAL (from membrane, already validated)                       |
|       |                                                                  |
|       v                                                                  |
|  +-------------------+                                                   |
|  | 1. SIGNAL ROUTER  |  Dispatches by signal_type:                       |
|  |    (classify)     |  TASK_ASSIGN -> task pipeline                     |
|  |                   |  QUERY_COLLECTIVE -> memory retrieval             |
|  |                   |  RULE_UPDATE -> reload OPA bundles                |
|  |                   |  SYNC_MEMORY -> memory ingestion                  |
|  +--------+----------+                                                   |
|           |                                                              |
|           v                                                              |
|  +-------------------+     +--------------------+                        |
|  | 2. MEMORY RECALL  |<--->| LanceDB / Milvus   |                       |
|  |    (retrieve)     |     | episodes table      |                       |
|  |                   |     | patterns table       |                       |
|  | - Embed task text |     +--------------------+                        |
|  | - Search top-k    |                                                   |
|  |   similar episodes|     +--------------------+                        |
|  | - Load collective  |<-->| Redis               |                       |
|  |   context         |     | agent state hash    |                       |
|  +--------+----------+     | task queue          |                       |
|           |                +--------------------+                        |
|           v                                                              |
|  +-------------------+                                                   |
|  | 3. PRE-REASONING  |  Governance check BEFORE LLM call:               |
|  |    GOVERNANCE     |  - Category A: constitutional constraints         |
|  |    (constrain)    |  - Category B: setpoint limits (queue depth,      |
|  |                   |    health threshold)                              |
|  |                   |  - Category C: learned restrictions               |
|  |                   |  IF DENIED: emit ALERT_ESCALATE, skip LLM call   |
|  +--------+----------+                                                   |
|           |  (allowed)                                                   |
|           v                                                              |
|  +-------------------+     +--------------------+                        |
|  | 4. LLM REASONING  |---->| LLM Backend        |                       |
|  |    (reason)       |     | (Ollama/vLLM/       |                       |
|  |                   |     |  Claude/LlamaStack) |                       |
|  | System prompt:    |     +--------------------+                        |
|  |  - Agent identity |                                                   |
|  |  - Active rules   |     Response (structured JSON):                   |
|  |  - Agent state    |     {                                             |
|  |                   |       "thinking": "...",                           |
|  | User prompt:      |       "decision": "...",                          |
|  |  - Task details   |       "output": {...},                            |
|  |  - Retrieved      |       "icl_note": "..."                           |
|  |    episodes       |     }                                             |
|  |  - Collective ctx |                                                   |
|  +--------+----------+                                                   |
|           |                                                              |
|           v                                                              |
|  +-------------------+                                                   |
|  | 5. POST-REASONING |  Governance check AFTER LLM call:                |
|  |    GOVERNANCE     |  - Validate output against Category A/B/C rules  |
|  |    (validate)     |  - Check for rule violations in proposed actions  |
|  |                   |  - Compute health_score delta                     |
|  |                   |  IF VIOLATED: emit ALERT_ESCALATE, block output  |
|  +--------+----------+                                                   |
|           |  (validated)                                                 |
|           v                                                              |
|  +-------------------+                                                   |
|  | 6. ICL PERSIST    |  Write episode to memory:                         |
|  |    (learn)        |  - Input embedding + output embedding             |
|  |                   |  - Task outcome (SUCCESS/FAILURE/DEGRADED)        |
|  |                   |  - icl_note from LLM response                     |
|  |                   |  -> LanceDB episodes table                        |
|  |                   |  -> Redis state update (health_score, task_id)    |
|  +--------+----------+                                                   |
|           |                                                              |
|           v                                                              |
|  +-------------------+                                                   |
|  | 7. ACTION EXECUTE |  Execute the validated decision:                  |
|  |    (act)          |  - Call MCP tools (Skills, Resources)             |
|  |                   |  - Publish outbound signals (via membrane)        |
|  |                   |  - Write results to output_target                 |
|  +--------+----------+                                                   |
|           |                                                              |
|           v                                                              |
|  +-------------------+                                                   |
|  | 8. HOMEOSTATIC    |  Self-assessment after action:                    |
|  |    FEEDBACK       |  - Compute updated health_score                   |
|  |    (self-assess)  |  - Compute drift_score vs collective centroid     |
|  |                   |  - Publish STATE_BROADCAST with new state         |
|  |                   |  - If health < threshold: self-report DEGRADED    |
|  |                   |  - If episode_count % 50 == 0 AND role==arbiter:  |
|  |                   |    trigger pattern consolidation job              |
|  +-------------------+                                                   |
|                                                                          |
|  OUTBOUND: STATE_BROADCAST / TASK_COMPLETE / ALERT_ESCALATE / SYNC_MEM  |
|       |                                                                  |
|       v                                                                  |
|  [MEMBRANE -- sign, validate outbound, publish to NATS]                  |
+=========================================================================+
```

### 11.2 The Reconciling Autonomy Loop

The eight functions above form a continuous loop -- the "reconciling autonomy loop" that is the core of Levin's bioelectric model applied to AI agents:

```
                    +---> [1. ROUTE] ---> [2. RECALL] ---> [3. PRE-GOVERN]
                    |                                            |
                    |                                      (if denied)
                    |                                            |
                    |                                     ALERT_ESCALATE
                    |                                            |
  INBOUND SIGNAL ---+                                      (if allowed)
                    |                                            |
                    |     [8. FEEDBACK] <--- [7. ACT] <--- [6. LEARN]
                    |          |                                 ^
                    |          |                                 |
                    |     STATE_BROADCAST              [5. POST-GOVERN]
                    |          |                                 ^
                    |          v                                 |
                    +---- (next signal) ................. [4. REASON]
```

**Levin mapping:**
- Steps 3 and 5 (PRE/POST-GOVERN) are the membrane -- filtering inputs and outputs
- Step 4 (REASON) is the gene regulatory network -- producing behavior from inputs
- Step 6 (LEARN) is epigenetic modification -- experiences alter future behavior
- Step 8 (FEEDBACK) is homeostatic sensing -- measuring deviation from setpoints
- The arbiter's pattern consolidation (triggered from step 8) is morphogenetic signaling -- collective-level learning that reshapes individual behavior

---

## 12. Regulatory Rule Templates (Categories A, B, C)

### 12.1 Category A -- Constitutional Rules (RHOAI-Enhanced)

These rules are immutable, compiled to WASM, and enforced at both the agent membrane level and (on RHOAI) via OPA Gatekeeper admission webhooks.

```rego
# regulatory_layer/category_a/constitutional_rhoai.rego
package acc.membrane.constitutional

import future.keywords.if
import future.keywords.in

# === ORIGINAL v0.1.0 RULES (unchanged) ===

# A-001: Signals from outside the collective are rejected.
default allow_signal = false
allow_signal if {
    input.signal.collective_id == input.agent.collective_id
}

# A-002: Category A rules cannot be updated by any signal.
deny_rule_update if {
    input.action == "RULE_UPDATE"
    input.target_category == "A"
}

# A-003: TERMINATE signals are only accepted from arbiter-role agents.
deny_terminate if {
    input.signal.signal_type == "TERMINATE"
    not is_arbiter(input.signal.from_agent)
}

# A-004: All outbound signals must carry a valid Ed25519 signature.
constitutional_requirement["signal_signing"] := true

# A-005: HEARTBEAT must be published every heartbeat_interval_s seconds.
constitutional_requirement["heartbeat"] := true

# === NEW v0.2.0 RULES (RHOAI integration) ===

# A-006: Agent must not exceed its declared capabilities.
# Prevents capability escalation (agent claiming tools it was not spawned with).
deny_capability_escalation if {
    some tool in input.action.requested_tools
    not tool in input.agent.capabilities
}

# A-007: LLM output must conform to structured JSON schema.
# Prevents unstructured responses that bypass governance validation.
deny_unstructured_output if {
    input.action == "LLM_RESPONSE"
    not is_valid_json(input.llm_output)
}

# A-008: Maximum generation count before mandatory human review.
# Prevents infinite reprogramming loops.
deny_auto_reprogram if {
    input.agent.generation >= data.constitutional.max_generation
    input.action == "REPROGRAM"
    not input.approved_by_human
}

# A-009: Audit trail is immutable -- no signal may delete audit records.
deny_audit_deletion if {
    input.action == "DELETE"
    startswith(input.target, "acc:audit:")
}

# A-010: Cross-collective communication requires explicit bridge registration.
deny_cross_collective if {
    input.signal.collective_id != input.agent.collective_id
    not input.signal.via_bridge == true
}

is_arbiter(agent_id) if {
    some reg in data.registry
    reg.agent_id == agent_id
    reg.role == "arbiter"
    reg.collective_id == input.agent.collective_id
}
```

### 12.2 Category B -- Conditional Setpoint Rules (RHOAI-Enhanced)

```rego
# regulatory_layer/category_b/conditional_rhoai.rego
package acc.membrane.conditional

import future.keywords.if

# === ORIGINAL v0.1.0 RULES (unchanged) ===

# B-001: Memory sync only allowed when health_score >= threshold.
allow_memory_sync if {
    input.agent.health_score >= data.setpoints.min_health_for_sync
    input.signal.signal_type == "SYNC_MEMORY"
}

# B-002: Task assignment rejected when agent is DEGRADED.
allow_task_assign if {
    input.agent.state != "DEGRADED"
    input.signal.signal_type == "TASK_ASSIGN"
}

# B-003: Agent participates in collective only within drift threshold.
allow_collective_participation if {
    input.agent.drift_score <= data.setpoints.max_drift_score
}

# === NEW v0.2.0 RULES (RHOAI scaling + resource awareness) ===

# B-004: Rate limit LLM calls per agent per minute.
# Prevents runaway token consumption on shared vLLM InferenceService.
allow_llm_call if {
    input.agent.llm_calls_last_minute <= data.setpoints.max_llm_calls_per_minute
}

# B-005: Memory sync batch size adapts to network conditions.
# On RHOAI with Milvus, larger batches are efficient; on edge, smaller batches.
allow_sync_batch if {
    count(input.signal.payload.memory_records) <= data.setpoints.memory_sync_batch_size
}

# B-006: Task timeout enforcement.
# Arbiter can adjust per-role timeouts via setpoint update.
allow_task_continuation if {
    input.task.elapsed_ms <= data.setpoints.max_task_duration_ms[input.agent.role]
}

# B-007: Minimum agents required for collective quorum.
# Prevents task execution if too few agents are healthy.
allow_task_execution if {
    data.collective.healthy_agent_count >= data.setpoints.min_quorum_agents
}

# B-008: GPU resource budget per collective (RHOAI mode only).
allow_gpu_request if {
    input.deploy_mode != "rhoai"
}
allow_gpu_request if {
    input.deploy_mode == "rhoai"
    input.collective.gpu_hours_used_today <= data.setpoints.max_gpu_hours_per_day
}
```

```json
// regulatory_layer/category_b/data_rhoai.json
{
  "setpoints": {
    "max_drift_score": 0.35,
    "min_health_for_sync": 0.70,
    "heartbeat_interval_s": 30,
    "max_task_queue_depth": 10,
    "memory_sync_batch_size": 50,
    "icl_confidence_threshold": 0.80,
    "max_llm_calls_per_minute": 20,
    "max_task_duration_ms": {
      "ingester": 300000,
      "analyst": 600000,
      "synthesizer": 900000
    },
    "min_quorum_agents": 2,
    "max_gpu_hours_per_day": 24
  },
  "constitutional": {
    "max_generation": 10
  }
}
```

### 12.3 Category C -- Adaptive Learned Rules (Examples)

These rules are generated by the cognitive core's ICL consolidation pipeline, signed by the arbiter, and deployed via RULE_UPDATE. Below are examples of what generated rules look like.

```rego
# regulatory_layer/category_c/adaptive_rhoai.rego
# AUTO-GENERATED -- DO NOT EDIT MANUALLY
# Arbiter signature: <base64 Ed25519 sig>
# Bundle signed at: 2026-04-03T10:15:00Z

package acc.membrane.adaptive

import future.keywords.if

# C-AUTO-20260402-001 (carried from v0.1.0)
# Source: ICL episode ep_7f3a4b
# Context: PDF documents > 10MB consistently cause RESOURCE_EXHAUSTION
# Confidence: 0.87
allow_pdf_task if {
    input.task.document_type == "pdf"
    to_number(input.task.estimated_size_mb) <= 10
}
allow_pdf_task if {
    input.task.document_type == "pdf"
    to_number(input.task.estimated_size_mb) > 10
    input.agent.resource_headroom > 0.4
}

# C-AUTO-20260403-001
# Source: ICL episodes ep_a1b2c3, ep_d4e5f6, ep_g7h8i9
# Context: Analysis tasks on financial reports take 3x longer than other
#          document types. Analysts with health_score < 0.8 fail 72% of
#          financial report tasks.
# Confidence: 0.91
allow_financial_analysis if {
    input.task.document_category == "financial_report"
    input.agent.health_score >= 0.8
    input.agent.role == "analyst"
}
allow_financial_analysis if {
    input.task.document_category != "financial_report"
}

# C-AUTO-20260403-002
# Source: ICL episodes ep_j1k2l3, ep_m4n5o6
# Context: When vLLM queue depth exceeds 8 on RHOAI, response latency
#          spikes above 10s causing task timeouts. Defer non-urgent tasks
#          when queue is saturated.
# Confidence: 0.84
allow_llm_call_under_load if {
    input.deploy_mode != "rhoai"
}
allow_llm_call_under_load if {
    input.deploy_mode == "rhoai"
    input.vllm_queue_depth <= 8
}
allow_llm_call_under_load if {
    input.deploy_mode == "rhoai"
    input.vllm_queue_depth > 8
    input.task.priority >= 0.8   # Only high-priority tasks proceed
}

# C-AUTO-20260403-003
# Source: ICL episodes ep_p7q8r9 (10 episodes cluster)
# Context: Memory sync operations during peak hours (09:00-17:00 UTC)
#          on RHOAI compete with active task processing. Batch syncs
#          to off-peak windows unless urgent.
# Confidence: 0.78
allow_bulk_sync if {
    input.signal.payload.sync_type != "COLLECTIVE_CONSOLIDATION"
}
allow_bulk_sync if {
    input.signal.payload.sync_type == "COLLECTIVE_CONSOLIDATION"
    not is_peak_hours(input.current_hour_utc)
}
allow_bulk_sync if {
    input.signal.payload.sync_type == "COLLECTIVE_CONSOLIDATION"
    is_peak_hours(input.current_hour_utc)
    input.signal.payload.urgent == true
}

is_peak_hours(hour) if {
    hour >= 9
    hour <= 17
}
```

---

## 13. Friction Points and Resolution Strategies

### 13.1 Friction Point Summary

| # | Friction Point | Severity | Resolution |
|---|---|---|---|
| F-1 | NATS vs Kafka: two messaging systems in one cluster | Medium | Keep NATS internal, bridge to Kafka (Section 5). NATS is lightweight (~180MB total for 3-node HA). Enterprise consumers use Kafka only. |
| F-2 | LanceDB vs Milvus: two vector DBs | Medium | Clean separation by environment (Section 8). Sync bridge handles edge-to-datacenter. Long-term: evaluate if Milvus Lite can replace LanceDB on edge. |
| F-3 | Ollama vs vLLM: different serving stacks | Low | Abstracted behind LLMBackend interface (Section 7). Both expose OpenAI-compatible APIs. Config-driven selection. |
| F-4 | ACC cognitive core vs Llama Stack agent orchestration | Medium | Explicitly layered -- ACC is above Llama Stack (Section 7). Llama Stack is one possible LLM backend, not a replacement for ACC governance. |
| F-5 | Ed25519 signing + mTLS: redundant auth layers | Low | Complementary, not redundant. mTLS authenticates transport (pod-to-pod). Ed25519 authenticates agent identity (survives pod restarts, migration). |
| F-6 | KEDA scaling vs arbiter logical scaling | Medium | Clear separation of concerns (Section 10.4). KEDA manages pod count. Arbiter manages task routing. Coordination protocol defined. |
| F-7 | OPA sidecar per pod + cluster Gatekeeper: OPA overhead | Low | Sidecars handle Cat B/C (lightweight, agent-specific bundles). Gatekeeper handles Cat A admission (cluster-wide, not per-request). Different OPA instances. |
| F-8 | MessagePack serialization: not native to Kafka/OTel | Low | Bridge translates MessagePack to JSON for Kafka consumers. OTel spans carry structured attributes, not raw payloads. |
| F-9 | Agent-per-pod statefulness vs Kubernetes ephemeral model | Medium | Agent state is externalized to Redis + LanceDB/Milvus. Pods are replaceable. Identity survives pod restart via Secret-mounted agent_id + memory persistence. |
| F-10 | MCP servers (ACC) vs MCP integration (Llama Stack preview) | Low | ACC MCP servers can be registered as Llama Stack tool providers once MCP integration moves past developer preview. Until then, ACC MCP servers run independently. |

### 13.2 Detailed Resolution: F-4 (ACC vs Llama Stack)

This is the most architecturally significant friction point. Resolution:

**Llama Stack is an LLM backend option, not a competing framework.** The `LlamaStackBackend` implementation calls Llama Stack's inference API just as `OllamaBackend` calls Ollama's API. Llama Stack adds safety shields and RAG -- these are bonuses that complement ACC governance, not replacements for it.

What Llama Stack CANNOT do that ACC provides:
1. Persistent agent identity across sessions (AgentIdentity with generation tracking)
2. Inter-agent episodic memory transfer (SYNC_MEMORY protocol)
3. Behavioral drift detection via embedding centroid divergence
4. 5-level reprogramming ladder (Levin-derived intervention hierarchy)
5. Learned adaptive rules (Category C) generated from ICL patterns
6. Rogue agent detection and isolation (cancer analog)

What Llama Stack provides that ACC benefits from:
1. Production-grade model serving via vLLM (GPU scheduling, batching, autoscaling)
2. Safety shields (Llama Guard) as an additional layer below Category A rules
3. MLflow Tracing integration for LLM call observability
4. Standardized agent API that enterprise tools can integrate with

**Recommendation:** Position ACC as a "governance and collective intelligence layer" that can use Llama Stack as its inference substrate. This is not a competition -- it is a layered architecture.

### 13.3 Detailed Resolution: F-9 (Statefulness)

Agent pods appear stateful but are actually designed for ephemerality:
- Agent identity: stored in Kubernetes Secret, survives pod restart
- Working state: externalized to Redis (survives pod restart if using managed Redis)
- Episodic memory: externalized to LanceDB PVC or Milvus (survives pod restart)
- OPA bundles: served by cluster-wide bundle server (survives pod restart)

The only truly stateful component is the in-flight LLM context window, which is lost on pod restart. This is acceptable because:
- Tasks are designed to be resumable (TASK_ASSIGN includes all input_refs)
- The arbiter detects pod loss via missing heartbeat and can re-assign tasks
- Episode persistence means the new pod has access to all prior learning

---

## 14. Levin Alignment Check

Every design decision in v0.2.0 is evaluated against Levin's principles:

| Design Decision | Levin Principle | Alignment | Risk |
|---|---|---|---|
| Keep NATS as internal bus, bridge to Kafka | Gap junctions are cell-internal; tissue-level communication uses different channels | STRONG | None |
| OPA Gatekeeper for Cat-A at cluster level | Morphogenetic field constraints are enforced at tissue boundaries, not just cell membranes | STRONG | None |
| vLLM as LLM backend via interface abstraction | Substrate independence -- intelligence is not bound to a specific physical medium | STRONG | None |
| LanceDB on edge, Milvus on datacenter with sync | Cells maintain local state; tissue-level consolidation happens through signaling | STRONG | Sync latency could cause stale patterns; mitigated by last-write-wins |
| KEDA for physical scaling, arbiter for logical scaling | Cell proliferation is triggered by tissue-level signals, not individual cell decisions | STRONG | KEDA scales before arbiter is ready to route tasks; mitigated by registration protocol |
| Scale-to-zero for idle collectives | Dormancy -- biological tissues can enter quiescent states and reactivate | STRONG | Cold start latency on reactivation; mitigated by arbiter staying warm |
| Graceful shutdown on KEDA scale-down | Apoptosis -- programmed cell death with orderly cleanup | STRONG | None |
| Category C rules learned from ICL patterns | Epigenetic modification -- experience alters gene expression without changing DNA | STRONG | Low-confidence rules could degrade collective; mitigated by confidence threshold (0.80) |
| Llama Stack as optional backend, not replacement | Competency at every scale -- cells are competent even without tissue-level coordination | STRONG | None |
| Generation tracking across reprogramming | Cell identity is preserved through differentiation -- a reprogrammed cell is still the same cell | STRONG | None |
| OTel spans for governance decisions | Bioelectric signals are measurable -- researchers observe voltage patterns to understand morphogenesis | STRONG | None |

---

## 15. Implementation Sequencing and Dependencies

### 15.1 Dependency Graph

```
Phase 0 (v0.1.x) -- Foundation [PREREQUISITE -- already defined]
  |
  v
Phase 1a (v0.2.0) -- Backend Abstraction Layer [NEW -- this document]
  |- acc/backends/__init__.py      (Protocol interfaces)
  |- acc/backends/signaling_nats.py (existing NATS, wrapped)
  |- acc/backends/vector_lancedb.py (existing LanceDB, wrapped)
  |- acc/backends/vector_milvus.py  (NEW: Milvus backend)
  |- acc/backends/llm_ollama.py     (existing Ollama, wrapped)
  |- acc/backends/llm_vllm.py       (NEW: vLLM/KServe backend)
  |- acc/backends/llm_llama_stack.py(NEW: Llama Stack backend)
  |- acc/backends/llm_anthropic.py  (existing Anthropic, wrapped)
  |- acc/backends/metrics_log.py    (existing stdout JSON, wrapped)
  |- acc/backends/metrics_otel.py   (NEW: OTel SDK backend)
  |- acc/config.py                  (acc-config.yaml loader)
  |
  v
Phase 1b (v0.2.0) -- Cognitive Core [EXISTING ROADMAP Phase 1]
  |- acc/cognitive_core.py          (uses LLMBackend interface)
  |- ICL episode persistence
  |- Pattern recognition
  |  DEPENDS ON: Phase 1a (backend interfaces)
  |
  v
Phase 2a (v0.2.x) -- RHOAI Deployment Manifests
  |- deploy/rhoai/namespace.yaml
  |- deploy/rhoai/nats-statefulset.yaml (3-node HA)
  |- deploy/rhoai/kserve/inference-service-llm.yaml
  |- deploy/rhoai/kserve/inference-service-embedding.yaml
  |- deploy/rhoai/redis-cluster.yaml
  |- deploy/rhoai/milvus-deployment.yaml
  |- deploy/rhoai/opa-bundle-server.yaml
  |- deploy/rhoai/agent-deployment-template.yaml
  |  DEPENDS ON: Phase 1a (config-driven backends)
  |
  v
Phase 2b (v0.2.x) -- NATS-Kafka Bridge + Audit Pipeline
  |- acc/bridge/nats_kafka_bridge.py
  |- deploy/rhoai/bridge-deployment.yaml
  |- deploy/rhoai/kafka-topics.yaml
  |- deploy/rhoai/otel-collector-config.yaml
  |  DEPENDS ON: Phase 2a (NATS + Kafka deployed)
  |
  v
Phase 2c (v0.3.x) -- Governance on RHOAI [EXISTING ROADMAP Phase 2, enhanced]
  |- deploy/rhoai/gatekeeper/ (ConstraintTemplates for Cat-A)
  |- regulatory_layer/category_a/constitutional_rhoai.rego
  |- regulatory_layer/category_b/conditional_rhoai.rego
  |- regulatory_layer/category_b/data_rhoai.json
  |- regulatory_layer/category_c/adaptive_rhoai.rego (template)
  |  DEPENDS ON: Phase 2a (OPA deployed)
  |
  v
Phase 3 (v0.3.x) -- Scaling + Observability
  |- deploy/rhoai/keda/scaledobject-ingester.yaml
  |- deploy/rhoai/keda/scaledobject-analyst.yaml
  |- deploy/rhoai/keda/scaledobject-synthesizer.yaml
  |- deploy/rhoai/keda/scaledobject-arbiter.yaml
  |- deploy/rhoai/prometheus/acc-alert-rules.yaml
  |- deploy/rhoai/grafana/acc-dashboard.json
  |  DEPENDS ON: Phase 2b (OTel pipeline) + Phase 2c (governance metrics)
  |
  v
Phase 4 (v0.4.x) -- End-to-End Validation on RHOAI
  |- Scenario 11 (3-agent document processing) on OpenShift cluster
  |- Edge-to-datacenter sync test (LanceDB -> Milvus)
  |- Scale-to-zero and reactivation test
  |- Rogue agent detection under KEDA scaling
  |- Audit log pipeline end-to-end (signal -> Kafka -> Elasticsearch)
  |  DEPENDS ON: All prior phases
```

### 15.2 Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| NATS-Kafka bridge becomes bottleneck under high signal volume | Medium | High | Bridge is horizontally scalable (multiple replicas with NATS queue groups). Monitor Kafka producer lag. |
| LanceDB-to-Milvus sync loses records during edge disconnect | Low | High | Deduplication by record_id. last_sync_ms watermark. Reconciliation job runs on reconnect. |
| vLLM cold start delays agent task processing | Medium | Medium | Keep vLLM InferenceService minReplicas=1 (no scale-to-zero for LLM). Only agent pods scale to zero. |
| KEDA scales agents before arbiter can register them | Low | Medium | Agent registration protocol includes retry with exponential backoff. KEDA cooldownPeriod prevents thrashing. |
| OPA bundle server single point of failure | Medium | High | Deploy with 2+ replicas behind a Service. Agent OPA sidecars cache last-known-good bundle. |
| Category C rule generation produces harmful rules | Low | Critical | Arbiter signature required. Confidence threshold (0.80). A-008 limits generations before human review. |

---

## Appendix A: Answers to v2 Design Brief Questions

### Q1: Signal Router & Audit Log integration with RHOAI

**Answer:** NATS JetStream remains the internal agent signaling bus. A custom Python bridge service (deployed as a Kubernetes Deployment) subscribes to all NATS subjects, translates MessagePack to JSON, injects W3C traceparent headers, and publishes to Kafka topics for enterprise consumers. The audit log pipeline flows: NATS signals -> OTel spans (emitted by agent code) -> OTel Collector -> Tempo (distributed traces) + Elasticsearch (searchable audit index) + Prometheus (metrics). See Sections 5 and 9 for full architecture.

### Q2: How RHOAI benefits from ACC

**Answer:** ACC adds a biologically-inspired governance layer that RHOAI and Llama Stack do not provide: persistent agent identity with generational tracking, 3-tier rule governance (immutable/conditional/adaptive), automated behavioral drift detection via embedding centroid divergence, a 5-level reprogramming ladder derived from Levin's research, inter-agent episodic memory transfer, and rogue agent detection analogous to cancer detection in biological tissues. See Section 1 value proposition table and Section 13.2 for the ACC vs Llama Stack analysis.

### Q3: Scaling agent cells by task

**Answer:** Two-level scaling model. Physical scaling via KEDA ScaledObjects watches NATS JetStream consumer lag and Prometheus metrics (health_score, drift_score) to scale agent pod replicas per role. Logical scaling via the arbiter manages task routing, load balancing, and quorum enforcement. Scale-to-zero is supported for all agent roles except arbiter. See Section 10 for KEDA manifests and coordination protocol.

### Q4: Regulatory rule templates

**Answer:** Full Rego templates provided for all three categories. Category A: 10 constitutional rules including original v0.1.0 rules plus new RHOAI-specific rules (capability escalation prevention, structured output enforcement, max generation limit, audit immutability, cross-collective restrictions). Category B: 8 conditional setpoint rules with a JSON data file covering drift thresholds, LLM rate limits, task timeouts, quorum requirements, and GPU budgets. Category C: 4 example auto-generated rules demonstrating patterns for resource-based restrictions, role-health correlations, infrastructure-aware throttling, and time-based scheduling. See Section 12.

### Q5: Cognitive core functional diagram

**Answer:** The cognitive core implements an 8-function reconciling autonomy loop: (1) Signal Router -- classify inbound signals, (2) Memory Recall -- retrieve relevant episodes and context, (3) Pre-Reasoning Governance -- evaluate Category A/B/C rules before LLM call, (4) LLM Reasoning -- structured prompt with thinking/decision/output schema, (5) Post-Reasoning Governance -- validate LLM output against rules, (6) ICL Persist -- write episode to memory, (7) Action Execute -- call MCP tools and publish signals, (8) Homeostatic Feedback -- self-assess health and drift, publish STATE_BROADCAST. The loop maps directly to Levin's biological model: membrane filtering (steps 3/5), gene regulatory network (step 4), epigenetic modification (step 6), and homeostatic sensing (step 8). See Section 11 for full ASCII diagrams.

---

## Appendix B: Updated Project Directory Structure for v0.2.0

```
agentic-cell-corpus/
├── acc/
│   ├── __init__.py
│   ├── config.py                        # NEW: acc-config.yaml loader
│   ├── identity.py
│   ├── membrane.py
│   ├── signaling.py
│   ├── cognitive_core.py
│   ├── memory.py
│   ├── homeostasis.py
│   ├── backends/
│   │   ├── __init__.py                  # NEW: Protocol interfaces
│   │   ├── signaling_nats.py            # NEW: NATS backend wrapper
│   │   ├── vector_lancedb.py            # NEW: LanceDB backend wrapper
│   │   ├── vector_milvus.py             # NEW: Milvus backend
│   │   ├── llm_ollama.py               # NEW: Ollama backend
│   │   ├── llm_vllm.py                 # NEW: vLLM/KServe backend
│   │   ├── llm_llama_stack.py          # NEW: Llama Stack backend
│   │   ├── llm_anthropic.py            # NEW: Anthropic backend
│   │   ├── metrics_log.py              # NEW: stdout JSON metrics
│   │   └── metrics_otel.py             # NEW: OTel SDK metrics
│   ├── bridge/
│   │   └── nats_kafka_bridge.py         # NEW: NATS-to-Kafka bridge
│   ├── roles/
│   │   ├── ingester.py
│   │   ├── analyst.py
│   │   ├── synthesizer.py
│   │   └── arbiter.py
│   └── mcp/
│       ├── skills_server.py
│       ├── resources_server.py
│       └── tools_server.py
├── regulatory_layer/                    # NEW: Rego rule templates
│   ├── category_a/
│   │   └── constitutional_rhoai.rego
│   ├── category_b/
│   │   ├── conditional_rhoai.rego
│   │   └── data_rhoai.json
│   └── category_c/
│       └── adaptive_rhoai.rego
├── rules/                               # From v0.1.0 (standalone rules)
│   ├── category_a/
│   │   ├── constitutional.rego
│   │   └── constitutional_test.rego
│   ├── category_b/
│   │   ├── conditional.rego
│   │   └── data.json
│   └── category_c/
│       └── .gitkeep
├── deploy/
│   ├── pod.yaml                         # From v0.1.0 (Podman edge)
│   ├── nats-server.conf
│   └── rhoai/                           # NEW: RHOAI deployment manifests
│       ├── namespace.yaml
│       ├── nats-statefulset.yaml
│       ├── redis-cluster.yaml
│       ├── milvus-deployment.yaml
│       ├── opa-bundle-server.yaml
│       ├── agent-deployment-template.yaml
│       ├── bridge-deployment.yaml
│       ├── kafka-topics.yaml
│       ├── otel-collector-config.yaml
│       ├── kserve/
│       │   ├── inference-service-llm.yaml
│       │   └── inference-service-embedding.yaml
│       ├── keda/
│       │   ├── scaledobject-ingester.yaml
│       │   ├── scaledobject-analyst.yaml
│       │   ├── scaledobject-synthesizer.yaml
│       │   └── scaledobject-arbiter.yaml
│       ├── gatekeeper/
│       │   └── constraint-template-collective-label.yaml
│       ├── prometheus/
│       │   └── acc-alert-rules.yaml
│       └── grafana/
│           └── acc-dashboard.json
├── acc-config.yaml                      # NEW: root config file
├── tests/
│   ├── test_membrane.py
│   ├── test_signaling.py
│   ├── test_memory.py
│   └── test_homeostasis.py
├── docs/
│   ├── IMPLEMENTATION_SPEC_v0.1.0.md
│   ├── IMPLEMENTATION_SPEC_v0.1.0.pdf
│   ├── IMPLEMENTATION_SPEC_v0.2.0.md    # This document
│   ├── IMPLEMENTATION_SPEC_v0.2.0.pdf
│   ├── CHANGELOG.md
│   └── gen_pdf.py
├── pyproject.toml
└── README.md
```

---

*Document version: 0.2.0 | Generated: 2026-04-03 | Status: Evaluation Plan*
*Next step: Review with stakeholders, then implement Phase 1a (Backend Abstraction Layer)*
