# Agentic Cell Corpus (ACC)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](pyproject.toml)
[![Go](https://img.shields.io/badge/Go-1.22-00ADD8)](operator/go.mod)
[![OLM Maturity](https://img.shields.io/badge/OLM-Level_3_Seamless_Upgrades-green)](operator/bundle/manifests/acc-operator.clusterserviceversion.yaml)
[![OpenShift](https://img.shields.io/badge/OpenShift-4.14%2B-red)](docs/operator-install-local.md)

A biologically-grounded runtime for **autonomous agent collectives** that operates under
**bounded agency** — the same codebase runs on a single Podman pod on a laptop and inside a
Red Hat OpenShift AI (RHOAI) namespace with KServe, Milvus, and Kafka, with no code branching
between modes. ACC wraps any LLM backend with a three-tier governance engine, persistent
episodic memory, and homeostatic rogue detection. It does not replace Kubernetes, vLLM, or
Llama Stack — it layers a minimal, edge-viable control loop above them and bridges to them
when a local cell connects to the datacenter.

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
| Edge and datacenter require different stacks | One `acc/` package, one `acc-config.yaml`; `deploy_mode: standalone` ↔ `deploy_mode: rhoai` is the only switch |
| Rogue detection = pod liveness/readiness probes | Cognitive rogue detection via embedding centroid divergence and heartbeat-absence scoring (cancer analog) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  standalone mode  (Podman, ≤ 8 GB RAM, consumer hardware)               │
│                                                                          │
│   acc-agent-ingester ──┐                                                 │
│   acc-agent-analyst  ──┤── NATS JetStream ── Redis ── LanceDB           │
│   acc-agent-arbiter  ──┘       (signaling)   (state)  (episodic memory) │
│                                                                          │
│   LLM backend: Ollama (local) | Anthropic API                           │
│   Governance:  OPA (in-process) · Category A/B/C rules                  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │  acc-config.yaml  deploy_mode switch
┌──────────────────────────────────▼──────────────────────────────────────┐
│  rhoai mode  (OpenShift AI, Kubernetes, datacenter)                      │
│                                                                          │
│   AgentCorpus CR ──► ACC Operator (Go, controller-runtime)              │
│                          │                                               │
│              ┌───────────┼───────────────┐                              │
│              ▼           ▼               ▼                               │
│         NATS (3-node) Redis (Sentinel) OPA Bundle Server                │
│              │                           │                               │
│         NATS-Kafka Bridge           Gatekeeper CTs                      │
│              │                                                           │
│   Agent Deployments × 5 roles (ingester · analyst · synthesizer ·       │
│                                  arbiter · observer)                     │
│              │                                                           │
│   KEDA ScaledObjects (optional) · KServe InferenceService (optional)    │
│   Milvus (external, probed) · Kafka (external, probed)                  │
└─────────────────────────────────────────────────────────────────────────┘
```

The `operator/` Go Operator manages the full RHOAI-mode lifecycle via two CRDs:
`AgentCorpus` (infrastructure + governance) and `AgentCollective` (per-collective agents
and scaling). Kafka, KEDA, OPA Gatekeeper, and RHOAI are **prerequisites detected at
runtime** — the operator degrades gracefully and emits Warning events when any are absent.

---

## Quick Start

### Standalone — Podman (2 commands)

```bash
# 1. Copy and configure the environment file
cp .env.example .env
# Edit .env: set ACC_OLLAMA_MODEL, ACC_ANTHROPIC_API_KEY, etc.

# 2. Start the collective
podman-compose -f deploy/podman-compose.yml up -d

# Watch agents reach REGISTERING → ACTIVE
podman logs -f acc-agent-ingester
```

Requirements: Podman ≥ 4.0, `podman-compose` ≥ 1.0.6, Ollama running locally (or set
`ACC_LLM_BACKEND=anthropic` and provide an API key).

### Kubernetes / OpenShift — Operator

```bash
# 1. Build and push the operator image
cd operator/
export IMG=quay.io/<your-org>/acc-operator:0.1.0
podman build -f Containerfile -t $IMG . && podman push $IMG

# 2. Deploy the operator (Method A — Kustomize, fastest)
make install && make deploy IMG=$IMG

# 3. Create the Category-A governance ConfigMap (required prerequisite)
kubectl create namespace acc-system
touch /tmp/category_a.wasm   # placeholder for local testing
kubectl create configmap acc-cat-a-wasm \
  --from-file=category_a.wasm=/tmp/category_a.wasm -n acc-system

# 4. Apply a sample corpus
kubectl apply -f operator/config/samples/acc_v1alpha1_agentcorpus_standalone.yaml

# 5. Watch it become Ready
kubectl get agentcorpus -n acc-system -w
```

See [`docs/operator-install-local.md`](docs/operator-install-local.md) for all three
deployment methods (Kustomize, OLM bundle, internal CatalogSource) and a full verification
checklist.

---

## Agent Roles

| Role | Function |
|------|----------|
| `ingester` | Receives external signals; normalises and routes them onto the NATS bus |
| `analyst` | Pattern recognition against episodic memory; semantic search via vector DB |
| `synthesizer` | Aggregates analyst outputs; prepares consolidated context for reasoning |
| `arbiter` | Governance authority: signs Category-C rules, coordinates rogue detection, approves reprogramming |
| `observer` | Passive telemetry; emits OTel spans and Prometheus metrics; zero bus writes |

---

## LLM Backends

| Backend | `acc-config.yaml` value | When to use |
|---------|------------------------|-------------|
| [Ollama](https://ollama.com) | `ollama` | Local inference; no API key; default for standalone mode |
| [Anthropic](https://anthropic.com) | `anthropic` | Cloud; best reasoning quality; requires `ANTHROPIC_API_KEY` |
| [vLLM / KServe](https://kserve.github.io) | `vllm` | RHOAI mode; GPU-backed `InferenceService`; OpenAI-compatible |
| [Llama Stack](https://llama-stack.readthedocs.io) | `llama_stack` | RHOAI mode; Llama Stack inference API; local embedding fallback |

Switching backends requires only a one-line change in `acc-config.yaml` (or the
`ACC_LLM_BACKEND` environment variable). All ACC modules are backend-agnostic — the
`build_backends()` factory in `acc/config.py` is the only place mode-switching occurs.

---

## Repository Layout

```
agentic-cell-corpus/
├── acc/                        # Python package — backends, config, agent lifecycle
│   ├── backends/               # 9 backend implementations (NATS, LanceDB, Milvus,
│   │   └── ...                 #   Ollama, Anthropic, vLLM, Llama Stack, log, OTel)
│   ├── config.py               # ACCConfig (Pydantic v2) + build_backends() factory
│   └── agent.py                # Agent lifecycle: REGISTERING → ACTIVE → DRAINING
├── operator/                   # Go Operator (controller-runtime v0.19, Operator SDK v1.36)
│   ├── api/v1alpha1/           # AgentCorpus + AgentCollective CRDs, webhooks
│   ├── internal/
│   │   ├── controller/         # Main reconcilers
│   │   ├── reconcilers/        # 11 sub-reconcilers (NATS, Redis, OPA, Kafka bridge, ...)
│   │   ├── templates/          # acc-config.yaml renderer (Go → Python-compatible YAML)
│   │   └── status/             # Phase computation + condition writers
│   ├── bundle/                 # OLM bundle (CSV, annotations, scorecard config)
│   └── config/                 # Kustomize manifests (CRDs, RBAC, manager, webhook, samples)
├── deploy/
│   ├── Containerfile.agent-core   # UBI10 / python-312 agent image (UID 1001)
│   └── podman-compose.yml         # Standalone: NATS + Redis + 3 agent roles
├── regulatory_layer/           # OPA Rego rules — Category A (constitutional),
│   └── ...                     #   B (conditional), C (adaptive templates)
├── tests/                      # 79 unit tests, 92% coverage; all infra mocked
├── docs/
│   ├── operator-install-local.md  # Operator deployment guide (3 methods)
│   ├── operator-certification.md  # Red Hat OperatorHub certification roadmap
│   ├── CHANGELOG.md
│   └── IMPLEMENTATION_SPEC_v0.2.0.md  # RHOAI integration design
├── openspec/                   # Planning artifacts (proposals, designs, task lists)
├── acc-config.yaml             # Default standalone config
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
| LLM | Ollama running locally **or** an Anthropic / vLLM API key |

### Kubernetes / OpenShift (Operator)

| Component | Minimum |
|-----------|---------|
| OpenShift | 4.14+ |
| Kubernetes | 1.27+ with OLM installed |
| Go | 1.22 (operator build only) |
| RAM on worker nodes | 16 GiB (NATS + Redis + 5 agent pods) |
| StorageClass | `ReadWriteOnce` PVCs for NATS and Redis |
| Container registry | `quay.io/<org>` or internal mirror |

The operator detects Kafka, KEDA, OPA Gatekeeper, and RHOAI at runtime. None are
required — the corpus degrades gracefully and emits Warning events when optional
prerequisites are absent.

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/operator-install-local.md`](docs/operator-install-local.md) | Step-by-step operator deployment: Kustomize, OLM bundle, CatalogSource |
| [`docs/operator-certification.md`](docs/operator-certification.md) | Red Hat OperatorHub certification roadmap: preflight, Connect submission, Konflux pipeline |
| [`docs/IMPLEMENTATION_SPEC_v0.2.0.md`](docs/IMPLEMENTATION_SPEC_v0.2.0.md) | RHOAI 3 integration design: compatibility matrix, dual-mode pattern, scaling strategy |
| [`docs/ACCv3.md`](docs/ACCv3.md) | ACC v3 design paper: sovereign edge-first architecture, biological grounding, non-goals |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Full implementation changelog |

---

## Contributing

Pull requests are welcome. All contributions are reviewed against two criteria before
merging:

### 1. Architectural alignment

Changes must be consistent with ACC's core design principles:

- **Dual-mode parity** — code must run unchanged in standalone Podman mode and in
  RHOAI/Kubernetes mode. `deploy_mode` is the only branching point; new
  if/else logic scattered across modules will be rejected.
- **Three-tier governance** — modifications to agent behaviour or rule handling must
  fit the Category A / B / C model. Category A rules are immutable by design;
  proposals to make them mutable will not be accepted.
- **Biologically-grounded model** — the agent lifecycle, reprogramming ladder, and
  rogue detection are grounded in Levin's bioelectric framework. Changes that reduce
  agents to stateless function calls (no persistent identity, no memory) contradict
  the architecture.
- **Sub-reconciler pattern** — operator changes must follow the ordered sub-reconciler
  pipeline in `agentcorpus_controller.go`. New infrastructure components belong in a
  new sub-reconciler under `internal/reconcilers/`, not in the main controller.

### 2. Target environment compatibility

All changes must work correctly on **both** target environments:

- **Standalone**: Podman, consumer hardware, ≤ 8 GB RAM, no Kubernetes runtime.
  Changes that require a Kubernetes API, cluster DNS, or a GPU will be rejected for
  this path.
- **OpenShift 4.14+ / Kubernetes 1.27+**: OCP restricted SCC (UID 1001 for agents,
  UID 65532 for the operator manager), UBI10 base images, OLM compatibility. Changes
  that use non-UBI base images or require `privileged: true` will be rejected.

### Process

For anything beyond a small bug fix, open a planning artifact first:

```
openspec/changes/<YYYYMMDD-short-description>/
├── proposal.md    # the WHY — problem statement, success criteria, scope
├── design.md      # the HOW — files changed, key decisions
└── tasks.md       # the WHAT — ordered implementation checklist
```

This surfaces design questions before code is written and keeps the change record
permanent in the repository. See [`openspec/changes/`](openspec/changes/) for examples.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
