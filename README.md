# Agentic Cell Corpus (ACC)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](pyproject.toml)
[![Go](https://img.shields.io/badge/Go-1.22-00ADD8)](operator/go.mod)
[![OLM Maturity](https://img.shields.io/badge/OLM-Level_3_Seamless_Upgrades-green)](operator/bundle/manifests/acc-operator.clusterserviceversion.yaml)
[![OpenShift](https://img.shields.io/badge/OpenShift-4.14%2B-red)](docs/operator-install-local.md)

A biologically-grounded runtime for **autonomous agent collectives** that operates under
**bounded agency** — the same codebase runs on a single Podman pod on a laptop, an edge node
in the field with intermittent connectivity, and inside a Red Hat OpenShift AI (RHOAI) namespace
with KServe, Milvus, and Kafka, with no code branching between modes. ACC wraps any LLM backend
with a three-tier governance engine, persistent episodic memory, cross-collective task delegation,
and homeostatic rogue detection.

---

## Why ACC

Modern agent frameworks treat agents as stateless tool-calling functions. ACC treats them as
persistent biological cells with memory, identity, and governance. The table below shows
the capabilities that gap implies:

| Without ACC | With ACC |
|---|---|
| Agent re-derives solutions from scratch on every invocation | ICL episodes consolidate into patterns → arbiter-signed Category-C rules; solutions accumulate on-device |
| Kill and replace the pod when an agent misbehaves | 5-level identity-preserving reprogramming ladder (Levin-derived); termination is the last resort |
| Governance = cluster admission control only | Three-tier rule engine: **A** constitutional (WASM, immutable) · **B** live-updatable setpoints · **C** adaptive, arbiter-signed rules learned from collective behaviour |
| Edge and datacenter require different stacks | One `acc/` package, one `acc-config.yaml`; `deploy_mode: standalone` ↔ `edge` ↔ `rhoai` is the only switch |
| Rogue detection = pod liveness/readiness probes | Cognitive rogue detection via embedding centroid divergence and heartbeat-absence scoring (cancer analog) |
| Multi-agent tasks confined to one collective | ACC-9 cross-collective bridge: `[DELEGATE:cid:reason]` marker → NATS bridge subject → 30s timeout with JetStream queuing for offline edge nodes |

See [`docs/value-proposition.md`](docs/value-proposition.md) for a detailed comparison with LangChain, CrewAI, AutoGen, and Haystack.

---

## Deploy Modes

| Mode | Target | Orchestration | LLM Default | Vector DB | Metrics |
|------|--------|--------------|-------------|-----------|---------|
| `standalone` | Developer laptop / CI | Podman Compose | Ollama (`llama3.2:3b`) | LanceDB | stdout log |
| `edge` | Edge node / MicroShift | MicroShift 4.14+ / K3s | Ollama (`llama3.2:3b`) | LanceDB (local NVMe) | stdout log |
| `rhoai` | OpenShift datacenter | OpenShift 4.14+ + RHOAI | vLLM / Llama Stack | Milvus | OTel Collector |

The `deploy_mode` field in `acc-config.yaml` (or `spec.deployMode` in the `AgentCorpus` CRD) is the only switch. No Python code branches on deploy mode — the `build_backends()` factory in `acc/config.py` is the single dispatch point.

---

## Architecture

> **Visual overview:** [`docs/architecture.svg`](docs/architecture.svg) — full 1200×960 diagram showing all five roles, three governance tiers, ACC-9 bridge protocol, edge/RHOAI deployment profiles, and the security roadmap.

### Standalone / Edge

```
┌─────────────────────────────────────────────────────────────────────────┐
│  standalone mode  (Podman, ≤ 8 GB RAM, consumer hardware)               │
│                                                                          │
│   acc-agent-ingester ──┐                                                 │
│   acc-agent-analyst  ──┤── NATS JetStream ── Redis ── LanceDB           │
│   acc-agent-arbiter  ──┘       (signaling)   (state)  (episodic memory) │
│                                                                          │
│   LLM backend: Ollama (local) | Anthropic API                           │
│   Governance:  WASM OPA (in-process) · Category A/B/C rules             │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ edge mode adds:
                                   │  NATS leaf node → hub port 7422
                                   │  JetStream bridge queue (offline-capable)
                                   │  Redis maxmemory 512 MB + allkeys-lru
```

### Cross-Collective Bridge (ACC-9)

```
Edge Node (sol-edge-01)                     Datacenter Hub (sol-dc-01)
────────────────────────                    ──────────────────────────
analyst: task needs 70B model
  │
  ├─ emit [DELEGATE:sol-dc-01:needs 70B]
  │
  ▼
acc.bridge.sol-edge-01.sol-dc-01.delegate ──► hub analyst processes
                                              result → acc.bridge.sol-dc-01.sol-edge-01.result
                                                         │
                                              ◄──────────┘
if hub unreachable:
  queue in JetStream acc.bridge.sol-edge-01.pending
  retry automatically on leaf reconnect
```

### RHOAI (OpenShift Datacenter)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  rhoai mode  (OpenShift AI, Kubernetes, datacenter)                       │
│                                                                            │
│   AgentCorpus CR ──► ACC Operator (Go, controller-runtime)               │
│                          │                                                 │
│              ┌───────────┼───────────────┐                               │
│              ▼           ▼               ▼                                │
│         NATS (3-node) Redis (Sentinel) OPA Bundle Server                 │
│              │                           │                                │
│         NATS-Kafka Bridge           Gatekeeper CTs                       │
│              │                                                            │
│   Agent Deployments × 5 roles (ingester · analyst · synthesizer ·        │
│                                  arbiter · observer)                      │
│              │                                                            │
│   KEDA ScaledObjects (optional) · KServe InferenceService (optional)     │
│   Milvus (external, probed) · Kafka (external, probed)                   │
│   OTel Collector + PrometheusRules + Grafana Dashboard (optional)        │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Standalone — Podman (2 commands)

```bash
# 1. Copy and configure
cp .env.example .env            # edit: set ACC_OLLAMA_MODEL, etc.

# 2. Start the collective
podman-compose -f deploy/podman-compose.yml up -d

# Watch agents reach ACTIVE
podman logs -f acc-agent-ingester
```

Requirements: Podman ≥ 4.0, `podman-compose` ≥ 1.0.6, Ollama running locally
(or set `ACC_LLM_BACKEND=anthropic` and provide an API key).

See [`docs/howto-standalone.md`](docs/howto-standalone.md) for the full setup guide including Redis auth, Ed25519 key generation, and LLM backend options.

### Edge — MicroShift / K3s

```bash
# Install CRDs + operator (no OLM / webhook required)
kubectl apply -f operator/config/crd/bases/
kubectl apply -f operator/config/rbac/
kubectl apply -f operator/config/manager/manager.yaml

# Create edge corpus (NATS leaf node, Redis eviction, no KEDA/OTel)
kubectl apply -f operator/config/samples/acc_v1alpha1_agentcorpus_edge.yaml

kubectl get agentcorpus -n acc-system -w
```

See [`docs/howto-edge.md`](docs/howto-edge.md) for hub connectivity, disconnected operation, and bridge delegation setup.

### RHOAI — OpenShift

```bash
# Build and push operator image
cd operator/
export IMG=quay.io/<your-org>/acc-operator:0.2.0
podman build -f Containerfile -t $IMG . && podman push $IMG

# Install via Kustomize
make install && make deploy IMG=$IMG

# Create Category-A governance WASM ConfigMap
kubectl create configmap acc-cat-a-wasm \
  --from-file=category_a.wasm=/path/to/category_a.wasm -n acc-system

# Apply a sample corpus (rhoai mode, Milvus + vLLM)
kubectl apply -f operator/config/samples/acc_v1alpha1_agentcorpus_rhoai.yaml

kubectl get agentcorpus -n acc-system -w
```

See [`docs/howto-rhoai.md`](docs/howto-rhoai.md) for the full operator setup, CRD reference, and KEDA/Gatekeeper/OTel wiring.

---

## Agent Roles

| Role | Function | CognitiveCore |
|------|----------|--------------|
| `ingester` | Receives external signals; normalises and routes onto the NATS bus | Yes |
| `analyst` | Pattern recognition against episodic memory; semantic search via vector DB | Yes |
| `synthesizer` | Aggregates analyst outputs; prepares consolidated context for reasoning | Yes |
| `arbiter` | Governance authority: signs Category-C rules, coordinates rogue detection, approves reprogramming | Yes |
| `observer` | Passive telemetry; emits OTel spans and Prometheus metrics; zero bus writes | No |

Each role is defined by a **role definition** — a versioned, Ed25519-signed document that shapes the agent's system prompt, task scope, allowed actions, and OPA setpoints. See [`docs/howto-role-infusion.md`](docs/howto-role-infusion.md).

---

## Governance Tiers

| Tier | Type | Enforcement | Update path |
|------|------|-------------|-------------|
| **Category A** | Constitutional (immutable) | WASM OPA in-process (<1 ms, no network) | Rebuild WASM + roll pods |
| **Category B** | Live-updatable setpoints | OPA bundle sidecar (hot-reload, configurable poll) | Push to OPA bundle server |
| **Category C** | Adaptive, arbiter-signed | Generated from ICL episode patterns; signed by arbiter Ed25519 key | Arbiter NATS publish + cryptographic verify |

---

## Cross-Collective Bridge (ACC-9)

Agent collectives can delegate tasks to peer collectives when the local LLM lacks the capability to handle them (e.g., a 3B-param edge model delegating to a 70B datacenter model).

**How it works:**
1. The LLM emits `[DELEGATE:target-collective-id:reason]` in its response.
2. `CognitiveCore` parses the marker and publishes to `acc.bridge.{from}.{to}.delegate`.
3. The target collective's agents process the task and publish a result to `acc.bridge.{to}.{from}.result`.
4. The originating agent receives the result within 30 seconds or falls back to local processing.
5. If the bridge is offline (edge mode, disconnected), the task queues in JetStream `acc.bridge.{cid}.pending` and is retried on reconnect.

**Governance gate (A-010):** Delegation is only active when `bridge_enabled: true` (from `ACC_BRIDGE_ENABLED` env var or `spec.edge.hubCollectiveId` in edge mode). The LLM cannot trigger cross-collective traffic in deployments that haven't opted in.

```yaml
# Enable bridge in acc-config.yaml
agent:
  collective_id: sol-01
  peer_collectives: [sol-02, sol-dc-01]
  bridge_enabled: true
```

---

## Terminal UI (TUI)

ACC ships a Textual terminal dashboard for observing live collective metrics and composing role definitions. It connects to NATS as a read-only observer — no Redis or LanceDB access required.

```bash
# Install TUI extras
pip install -e ".[tui]"

# Launch
export ACC_NATS_URL=nats://localhost:4222
export ACC_COLLECTIVE_ID=sol-01
acc-tui
```

The TUI has two screens (switch with `Tab`):
- **Dashboard** — live agent cards (drift score sparkbar, reprogramming ladder, staleness), governance panel (Cat-A/B/C triggers), memory panel (ICL episodes, patterns), LLM metrics (p95 latency, token utilisation, blocked tasks)
- **Infuse** — compose a role definition (purpose, persona, task types, seed context, Cat-B overrides), publish as a `ROLE_UPDATE` to NATS, monitor arbiter approval status and role history

See [`docs/howto-tui.md`](docs/howto-tui.md) for the full guide including deployment as a container pod.

---

## Security

ACC's security hardening follows a phased approach. The first two phases are implemented:

| Phase | Controls | Status |
|-------|----------|--------|
| **0a** — Ed25519 verification | `RoleStore.apply_update()` cryptographically verifies arbiter signatures; unsigned ROLE_UPDATE rejected | ✅ Implemented |
| **0b** — Redis auth | `requirepass` + per-agent Secret injection; `ACC_REDIS_PASSWORD` wired into all Redis clients | ✅ Implemented |
| **0c** — NATS NKeys | Per-role NKey authentication; publish/subscribe permission matrix including bridge subjects | 🔲 Planned |
| **1** — Cilium L7 NetworkPolicy | eBPF-enforced agent egress rules (NATS, Redis, LLM backends, NATS leaf port 7422) | 🔲 Planned |
| **2** — SPIFFE/SPIRE mTLS | Stable cryptographic agent identity; mTLS between NATS/Redis/agents; auto-rotating SVIDs | 🔲 Planned |
| **3** — Tetragon Cat-A | Kernel eBPF event stream → real WASM Cat-A evaluation; `execve`/`connect` governance triggers | 🔲 Planned |
| **4** — Hardened Standalone | NKeys + self-signed CA mTLS for Podman mode; no SPIRE/Tetragon dependency | 🔲 Planned |

Quick setup for the implemented phases:
```bash
# Phase 0a — Ed25519 arbiter verify key
export ACC_ARBITER_VERIFY_KEY=<base64-encoded-raw-32-byte-ed25519-public-key>

# Phase 0b — Redis auth
export ACC_REDIS_URL=redis://localhost:6379
export ACC_REDIS_PASSWORD=$(openssl rand -hex 32)
```

See [`docs/security-hardening.md`](docs/security-hardening.md) for the complete security architecture, governance layer (Cat-A/B/C Rego rules), and phase-by-phase implementation plan.

---

## LLM Backends

| Backend | Config value | When to use |
|---------|-------------|-------------|
| [Ollama](https://ollama.com) | `ollama` | Local inference; no API key; default for standalone and edge |
| [Anthropic](https://anthropic.com) | `anthropic` | Cloud; best reasoning quality; requires `ACC_ANTHROPIC_API_KEY` |
| [vLLM / KServe](https://kserve.github.io) | `vllm` | RHOAI mode; GPU-backed InferenceService; OpenAI-compatible |
| [Llama Stack](https://llama-stack.readthedocs.io) | `llama_stack` | RHOAI mode; Llama Stack inference API |

Switch backends with one line:
```yaml
llm:
  backend: anthropic    # was: ollama
```
or `export ACC_LLM_BACKEND=anthropic`. No other code changes required.

---

## Environment Variables

| Variable | Config field | Default | Description |
|---|---|---|---|
| `ACC_DEPLOY_MODE` | `deploy_mode` | `standalone` | Deployment profile |
| `ACC_AGENT_ROLE` | `agent.role` | `ingester` | Role for this agent pod |
| `ACC_COLLECTIVE_ID` | `agent.collective_id` | `sol-01` | Collective identifier |
| `ACC_NATS_URL` | `signaling.nats_url` | `nats://localhost:4222` | NATS server URL |
| `ACC_NATS_HUB_URL` | `signaling.hub_url` | _(empty)_ | NATS leaf hub URL (edge only) |
| `ACC_LANCEDB_PATH` | `vector_db.lancedb_path` | `/app/data/lancedb` | LanceDB data directory |
| `ACC_MILVUS_URI` | `vector_db.milvus_uri` | _(empty)_ | Milvus URI (rhoai mode) |
| `ACC_LLM_BACKEND` | `llm.backend` | `ollama` | LLM backend |
| `ACC_OLLAMA_BASE_URL` | `llm.ollama_base_url` | `http://localhost:11434` | Ollama server URL |
| `ACC_OLLAMA_MODEL` | `llm.ollama_model` | `llama3.2:3b` | Ollama model name |
| `ACC_ANTHROPIC_MODEL` | `llm.anthropic_model` | `claude-sonnet-4-6` | Anthropic model |
| `ACC_VLLM_INFERENCE_URL` | `llm.vllm_inference_url` | _(empty)_ | vLLM endpoint |
| `ACC_METRICS_BACKEND` | `observability.backend` | `log` | Metrics backend |
| `ACC_ROLE_PURPOSE` | `role_definition.purpose` | _(empty)_ | Role purpose override |
| `ACC_ROLE_PERSONA` | `role_definition.persona` | `concise` | Persona style |
| `ACC_ARBITER_VERIFY_KEY` | `security.arbiter_verify_key` | _(empty)_ | Base64 Ed25519 public key |
| `ACC_REDIS_URL` | `working_memory.url` | _(empty)_ | Redis connection URL |
| `ACC_REDIS_PASSWORD` | `working_memory.password` | _(empty)_ | Redis password |
| `ACC_PEER_COLLECTIVES` | `agent.peer_collectives` | _(empty)_ | Comma-separated delegation targets |
| `ACC_HUB_COLLECTIVE_ID` | `agent.hub_collective_id` | _(empty)_ | Hub collective ID (edge) |
| `ACC_BRIDGE_ENABLED` | `agent.bridge_enabled` | `false` | Enable cross-collective delegation |

---

## Repository Layout

```
agentic-cell-corpus/
├── acc/                        # Python package — backends, config, agent lifecycle
│   ├── backends/               # 9 backend implementations (NATS, LanceDB, Milvus,
│   │   └── ...                 #   Ollama, Anthropic, vLLM, Llama Stack, log, OTel)
│   ├── tui/                    # Textual terminal UI (acc-tui entry point)
│   │   ├── app.py              # ACCTUIApp: NATS lifecycle, screen registry, drain loop
│   │   ├── client.py           # NATSObserver: HEARTBEAT/TASK_COMPLETE/ALERT_ESCALATE routing
│   │   ├── models.py           # AgentSnapshot + CollectiveSnapshot data models
│   │   └── screens/
│   │       ├── dashboard.py    # Live agent cards, governance/memory/LLM panels
│   │       └── infuse.py       # Role definition form + ROLE_UPDATE publish
│   ├── config.py               # ACCConfig (Pydantic v2) + build_backends() factory
│   ├── agent.py                # Agent lifecycle: REGISTERING → ACTIVE → DRAINING
│   ├── cognitive_core.py       # CognitiveCore: 7-step pipeline, Cat-A/B/C, delegation
│   ├── role_store.py           # RoleStore: 4-tier load, ROLE_UPDATE hot-reload, Ed25519
│   └── signals.py              # NATS subject naming (intra-collective + bridge subjects)
├── operator/                   # Go Operator (controller-runtime v0.19, Operator SDK v1.36)
│   ├── api/v1alpha1/           # AgentCorpus + AgentCollective CRDs (standalone/edge/rhoai)
│   ├── internal/
│   │   ├── reconcilers/        # 11+ sub-reconcilers (NATS leaf, Redis eviction, OPA, ...)
│   │   ├── templates/          # acc-config.yaml + nats.conf renderer
│   │   └── status/             # Phase computation + condition writers
│   └── config/                 # Kustomize manifests + sample CRs
├── deploy/
│   ├── Containerfile.agent-core   # UBI10 / python-312 agent image (UID 1001)
│   └── podman-compose.yml         # Standalone: NATS + Redis + 3 agent roles
├── regulatory_layer/           # OPA Rego rules — Category A/B/C
├── tests/                      # 127 unit tests; all infra mocked
├── docs/
│   ├── howto-standalone.md        # Podman setup, env vars, Redis auth, Ed25519
│   ├── howto-edge.md              # Edge node setup, hub connectivity, bridge delegation
│   ├── howto-rhoai.md             # OpenShift operator, CRD reference, GPU inference
│   ├── howto-role-infusion.md     # Role definition, 4-tier load, hot-reload, signing
│   ├── value-proposition.md       # Why ACC vs LangChain/CrewAI/AutoGen/Haystack
│   ├── operator-install-local.md  # Operator deployment guide (3 methods)
│   ├── operator-certification.md  # Red Hat OperatorHub certification roadmap
│   ├── CHANGELOG.md
│   └── IMPLEMENTATION_SPEC_v0.2.0.md  # RHOAI integration design
├── openspec/                   # Planning artifacts (proposals, designs, task lists)
├── acc-config.yaml             # Default standalone config (annotated)
└── pyproject.toml
```

---

## Requirements

### Standalone (Podman)

| Component | Minimum |
|-----------|---------|
| Python | 3.12 |
| Podman | 4.0 |
| podman-compose | 1.0.6 |
| RAM | 4 GB (8 GB recommended with local Ollama) |
| LLM | Ollama running locally **or** Anthropic API key |

### Edge (MicroShift / K3s)

| Component | Minimum |
|-----------|---------|
| MicroShift | 4.14+ (or K3s any) |
| RAM | 4 GB |
| Storage | 32 GB NVMe |
| Go | 1.22 (operator build only) |

### RHOAI (OpenShift Datacenter)

| Component | Minimum |
|-----------|---------|
| OpenShift | 4.14+ |
| RHOAI / ODH | 2.x |
| Go | 1.22 (operator build only) |
| RAM per worker | 16 GiB |
| StorageClass | ReadWriteOnce PVCs |

Optional prerequisites (detected at runtime, graceful degradation when absent): KEDA, OPA Gatekeeper, Prometheus Operator, Kafka, RHOAI/KServe.

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/architecture.svg`](docs/architecture.svg) | Full architecture diagram: 5 roles, Cat-A/B/C, ACC-9 bridge, edge/RHOAI, security roadmap |
| [`docs/howto-standalone.md`](docs/howto-standalone.md) | Podman Compose setup, env vars, Redis auth, Ed25519 keys |
| [`docs/howto-edge.md`](docs/howto-edge.md) | Edge node setup, NATS leaf topology, bridge delegation, offline operation |
| [`docs/howto-rhoai.md`](docs/howto-rhoai.md) | OpenShift operator install, CRD reference, GPU inference, KEDA/Gatekeeper/OTel |
| [`docs/howto-role-infusion.md`](docs/howto-role-infusion.md) | Role definition schema, 4-tier load order, ROLE_UPDATE hot-reload, Ed25519 signing |
| [`docs/howto-tui.md`](docs/howto-tui.md) | Terminal UI: dashboard screen, infuse screen, container deployment, keyboard shortcuts |
| [`docs/security-hardening.md`](docs/security-hardening.md) | Complete security architecture: Cat-A/B/C Rego rules, Phase 0a–4 implementation plan |
| [`docs/value-proposition.md`](docs/value-proposition.md) | Comparison with LangChain, CrewAI, AutoGen, Haystack |
| [`docs/operator-install-local.md`](docs/operator-install-local.md) | Detailed operator deployment (Kustomize, OLM bundle, CatalogSource) |
| [`docs/operator-certification.md`](docs/operator-certification.md) | Red Hat OperatorHub certification roadmap |
| [`docs/IMPLEMENTATION_SPEC_v0.2.0.md`](docs/IMPLEMENTATION_SPEC_v0.2.0.md) | RHOAI 3 integration design: compatibility matrix, dual-mode pattern |
| [`docs/ACCv3.md`](docs/ACCv3.md) | ACC v3 design paper: sovereign edge-first architecture, biological grounding |

---

## Contributing

Pull requests are welcome. All contributions are reviewed against two criteria before merging:

### 1. Architectural alignment

- **Three-mode parity** — code must run unchanged in standalone Podman, edge, and RHOAI modes. `deploy_mode` is the only branching point.
- **Three-tier governance** — modifications to agent behaviour or rule handling must fit the Category A / B / C model. Category A rules are immutable by design.
- **Biologically-grounded model** — the agent lifecycle, reprogramming ladder, and rogue detection are grounded in Levin's bioelectric framework.
- **Sub-reconciler pattern** — operator changes must follow the ordered sub-reconciler pipeline. New infrastructure components belong in a new sub-reconciler under `internal/reconcilers/`.

### 2. OpenSpec-first changes

For anything beyond a small bug fix, open a planning artifact first:

```
openspec/changes/<YYYYMMDD-short-description>/
├── proposal.md    # the WHY — problem statement, success criteria, scope
├── design.md      # the HOW — files changed, key decisions
└── tasks.md       # the WHAT — ordered implementation checklist
```

See [`openspec/changes/`](openspec/changes/) for examples.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
