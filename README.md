# Agentic Cell Corpus (ACC)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](pyproject.toml)
[![Go](https://img.shields.io/badge/Go-1.23-00ADD8)](operator/go.mod)
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

## Recent updates

Highlights from the current `0.5.x` cycle — see [`CHANGELOG.md`](CHANGELOG.md) for the full list. Everything below ships **additive and opt-in**; existing `acc-config.yaml` files and deployments are byte-for-byte unchanged.

| Feature | What it is | Status |
|---|---|---|
| **Role & package ecosystem** | The 43 movable roles moved out of core into signed, versioned `@acc/*` **family packs** served from the public [`acc-ecosystem`](https://github.com/flg77/acc-ecosystem) registry. `acc-pkg` builds/signs/verifies/installs `.accpkg` bundles; `collective.yaml` `required_packages:` + a dual-source loader fetch and verify them at boot; **Marketplace** + **Catalog** panes (TUI/WebGUI) and the [`acc-podman-desktop`](https://github.com/flg77/acc-podman-desktop) extension are the discovery surfaces. Core keeps only the 7 CONTROL roles. See [**Role & Package Ecosystem**](#role--package-ecosystem). | ✅ Landed (Stage 1 + Stage 2 cutover) |
| **Compliance governance & frameworks** | The full governance surface in both UIs: browse the **Category A/B/C rule layers**, import regulatory **frameworks** (NIST AI RMF, SOC 2, EU AI Act, …) and **run a gap scan** (coverage %, gaps), then review **arbiter-proposed Category-C rule proposals** learned from collective violations — approve/reject with the action stamped to your identity. Standalone-first; shared report/proposal store between `acc-tui` and `acc-webgui`. | ✅ Landed (PR-Z1/Z2/Z3) |
| **Per-agent models (multimodel)** | A central `models.yaml` registry lets each agent role run on a different backend/model (e.g. a `reviewer` on a powerful model driving a critic loop). The TUI/WebGUI Ecosystem surfaces the registry; `AgentSpec.model` selects per role. | ✅ Landed (PR-MM1/2/3) |
| **Self-reflective memory** | An out-of-band consolidation loop distils episodic memory into durable `memory_notes` an agent reads on the hot path — opt-in per role via `memory_reflection`. | ✅ Landed (PR-MEM1/2/3) |
| **Prompt caching** | A stable cacheable role/RAG prefix plus an optional per-backend cache hint (Anthropic `cache_control`); best-effort cache metrics in the Performance pane. Opt-in via `ACC_LLM_ENABLE_PROMPT_CACHE`. | ✅ Landed (PR-CA1/2/3) |
| **Golden-prompt diagnostics** | A YAML golden-prompt suite + CLI/TUI runner and a scheduled history runner; the WebGUI Diagnostics screen lists the suite. | ✅ Landed (PR-K/N/O) |
| **acc-webgui** | An optional FastAPI + React web frontend — feature parity with the `acc-tui` terminal UI plus enhanced tracing views (task-step waterfall, PLAN DAG, tamper-evident audit-chain timeline). Mirrors the latest TUI surfaces (governance layers, frameworks + gap scan, rule proposals, model registry, golden-prompt diagnostics, Enter-to-send Prompt). Reuses the TUI's data layer; capability-tiered auth (oauth-proxy / OIDC / mTLS / htpasswd / token). Opt-in: a separate container + compose profile. | ✅ Landed (proposal acc-webgui) |
| **Runtime-evidence Cat-A** | Provider-agnostic kernel-event governance — the operator detects whichever runtime-security tool the cluster runs (RHACS / Falco / Tetragon) plus NetObserv for network flows, a bridge normalises `execve`/`openat`/`connect` events onto NATS, and CognitiveCore folds them into Category-A. Observe-by-default. Opt-in via `governance.runtimeEvidence.enabled`. | ✅ Landed (proposal 015) |
| **Kernel-enforced exec sandbox (OpenShell)** | The *enforcement* complement to Runtime-evidence Cat-A — turns Cat-A/B/C from *evaluated-at-dispatch* into *enforced-at-the-kernel* for the untrusted surface. An opted-in agent's code execution (`shell_exec` / `python_exec`) is delegated into a per-agent [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandbox — Landlock + seccomp + per-binary egress — carrying the corpus's Cat-A/B/C policy. The operator provisions it (policy `ConfigMap` + `openshell sandbox create` initContainer + OIDC/SPIFFE gateway auth); the runtime (`acc/sandbox`) delegates exec **fail-closed** (never falls back to un-caged local execution). Default-OFF, inert unless `spec.sandbox.gatewayURL` is set. | ✅ Landed (Model 2, v0.5.49) · live kernel-denial smoke pending |
| **L7 / eBPF NetworkPolicy** | Capability-tiered network isolation for agent pods — Tier 1 standard `NetworkPolicy` (the portable L3/L4 must-have), Tier 2 FQDN egress (OVN `EgressFirewall` or Cilium), Tier 3 Cilium L7. The operator emits the highest tier the cluster's CNI can enforce; honest `CNIDoesNotEnforce` status on K3s/Flannel. Opt-in via `networkPolicy.enabled`. | ✅ Landed (proposal 014) |
| **NATS NKeys** | Per-role NKey authentication with a server-enforced publish/subscribe permission matrix (six agent roles + `tui` + `leaf` identities); the `acc.{cid}.task` subject split into `.task.assign` / `.task.complete`. Opt-in via `security.nkey.enabled`. | ✅ Landed (proposal 013) |
| **SPIFFE workload identity** | Agents authenticate `ROLE_UPDATE` signatures with SPIRE-issued JWT-SVIDs instead of a static Ed25519 key — operator-issued `ClusterSPIFFEID` resources, a `spiffe-helper` sidecar, agent-side verification, edge nested/federated topologies with offline survival. Opt-in via `security.signing_mode: spiffe`. | ✅ Landed (proposals 011 + 012) |
| **Bi-directional role-definition sync** | `role_sync.role_source: files \| crd \| mirror` keeps `roles/<id>/role.yaml` and the `AgentCollective` CRD in step, with mirror-mode conflict detection over NATS. | ✅ Landed (proposal 010) |
| **TUI usability hardening** | Prompt cancel-on-timeout, `role.md` narrative rendering, role-directory file-watcher, the Configuration pane (pane 8). | ✅ Landed (proposal 003, v0.2.0) |

**Planned next**: Phase 4 (hardened standalone — NKeys + self-signed CA mTLS for Podman); the v0.5.0 `rhoai` default flip to `signing_mode: spiffe`; NATS mTLS using the X.509-SVID; the offline-action agent wire-up. ACC remains pre-1.0.

---

## Architecture

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

These are the canonical collective-pipeline roles. After the Stage 2 cutover, core ships only the **7 CONTROL roles** — `arbiter`, `assistant`, `compliance_officer`, `ingester`, `observer`, `orchestrator`, `reviewer` — while `analyst`, `synthesizer`, and every other role ship as packages from the ecosystem (below).

---

## Role & Package Ecosystem

Core ships the **7 CONTROL roles** (the table above). The other **43 movable
roles** — coding-agent variants, research, business, and DevOps personas — now
ship as signed, versioned **role packages** from a public registry, so you
install only the roles you need and can publish your own.

> **End-to-end walkthrough:** [`docs/howto-build-deploy-infuse.md`](docs/howto-build-deploy-infuse.md)
> covers the whole lifecycle (build → publish → deploy → infuse → verify) from
> the CLI, TUI, and WebGUI.

**How it works**

- **`acc-pkg`** builds, signs, verifies, and installs `.accpkg` bundles — a
  byte-deterministic tarball carrying a role definition, its bundled
  skills/MCPs, behavioral + safety evals, and optional Cat-A/B/C policy bounds.
- **Catalogs** (`catalogs.yaml`, layered system → user → workspace) point ACC at
  one or more registries. Every package is cosign-verified against the catalog's
  `required_signer` before it installs — the signing floor is non-negotiable.
- **Declare packages** in `collective.yaml` under `required_packages:`; ACC's
  boot-time fetch resolves, verifies, and unpacks them before agents spawn. The
  **dual-source loader** prefers an installed package over the in-tree fallback.
- **Discover + install** from the **Marketplace** and **Catalog admin** panes in
  both the TUI and WebGUI, or with `acc-pkg install @acc/research-roles@^1.0`.

**The registry — [`flg77/acc-ecosystem`](https://github.com/flg77/acc-ecosystem)**

The canonical registry serves the `@acc/*` role packs:

| Pack | Roles |
|---|---|
| `@acc/workspace-roles` | coding-agent + 5 variants, analyst, synthesizer (8) |
| `@acc/research-roles` | research planner, critic, strategist, economist, … (6) |
| `@acc/devops-roles` | data, devops, ML, and security engineers (4) |
| `@acc/hr-roles` · `@acc/finance-roles` · `@acc/sales-roles` · `@acc/marketing-roles` · `@acc/legal-roles` · `@acc/support-roles` · `@acc/operations-roles` | the corporate domains — the former `@acc/business-roles` monolith, split so you install only what you need (29 roles total) |
| `@acc/business-roles@^2.0` | **umbrella** — `depends_on` all seven corporate packs; one entry installs the whole suite (`@acc/business-roles@^1.0` still resolves the frozen 25-role monolith) |

Point a catalog at it:

```yaml
# .acc/catalogs.yaml
catalogs:
  - id: acc-canonical
    tier: trusted
    mode: https
    url: https://flg77.github.io/acc-ecosystem
    required_signer:
      issuer: https://token.actions.githubusercontent.com
      subject_pattern: "^https://github\\.com/flg77/acc-ecosystem/"
    priority: 100
```

See [`examples/catalogs.yaml`](examples/catalogs.yaml) for the full layered example (trusted / community / self tiers).

**Create your own role pack** — `acc-pkg init` → author the role → write evals →
sign keyless via GitHub Actions OIDC → publish, in under an hour. See
[`docs/CONTRIBUTING-ROLE.md`](docs/CONTRIBUTING-ROLE.md).

**Migrating from in-tree roles** — operators upgrading across the Stage 2 cutover
declare `required_packages:` once; the dual-source loader does the rest. See
[`docs/MIGRATING-FROM-INTREE.md`](docs/MIGRATING-FROM-INTREE.md).

**Podman Desktop** — the [`acc-podman-desktop`](https://github.com/flg77/acc-podman-desktop)
extension brings up and governs an ACC collective (and browses roles/skills/MCPs)
from inside Podman Desktop, alongside Podman AI Lab.

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

The TUI panes (switch with the nav bar / `Tab`):
- **Dashboard** — live agent cards (drift score sparkbar, reprogramming ladder, staleness), governance panel (Cat-A/B/C triggers), memory panel (ICL episodes, patterns), LLM metrics (p95 latency, token utilisation, blocked tasks)
- **Infuse** — compose a role definition (purpose, persona, task types, seed context, Cat-B overrides), publish as a `ROLE_UPDATE` to NATS, monitor arbiter approval status and role history
- **Prompt** — drive an agent directly (Enter-to-send, operating-mode aware: PLAN / ACCEPT_EDITS / ASK_PERMISSIONS / AUTO), optionally scoping a workspace directory
- **Compliance** — the live oversight queue plus the Category A/B/C governance layers, regulatory frameworks + gap scan, and the arbiter rule-proposal review surface
- **Ecosystem** — collective roles + the `models.yaml` model registry; infuse roles
- **Performance** — LLM/token metrics including best-effort prompt-cache stats
- **Comms** — cross-collective bridge / signalling activity
- **Configuration** — the running `acc-config.yaml` view
- **Diagnostics** — the golden-prompt suite + runner

See [`docs/howto-tui.md`](docs/howto-tui.md) for the full guide including deployment as a container pod.

---

## Security

ACC's security hardening follows a phased approach. Phases 0a, 0b, 0c, 1, 2, and 3 are implemented (0c, 1, 2, and 3 ship opt-in); phase 4 is planned:

Phases marked _opt-in_ are additive — they change no behaviour until
explicitly enabled, so the Status column reports `Implemented` for all
of them; the "opt-in" switch is named in each row's Controls cell.

| Phase | Controls | Status |
|:------|:---------|:------:|
| **0a** — Ed25519 verification | `RoleStore.apply_update()` cryptographically verifies arbiter signatures; unsigned ROLE_UPDATE rejected | ✅ Implemented |
| **0b** — Redis auth | `requirepass` + per-agent Secret injection; `ACC_REDIS_PASSWORD` wired into all Redis clients | ✅ Implemented |
| **0c** — NATS NKeys | Per-role NKey authentication; server-enforced publish/subscribe permission matrix including bridge subjects; `tui` + `leaf` identities. _Opt-in_ via `security.nkey.enabled` | ✅ Implemented |
| **1** — L7 / eBPF NetworkPolicy | Capability-tiered network isolation for agent pods: Tier 1 standard `NetworkPolicy` (L3/L4, the portable must-have), Tier 2 FQDN egress (OVN `EgressFirewall` or Cilium), Tier 3 Cilium L7. _Opt-in_ via `networkPolicy.enabled` | ✅ Implemented |
| **2** — SPIFFE workload identity | SPIRE-issued JWT-SVIDs sign/verify `ROLE_UPDATE`; operator-issued `ClusterSPIFFEID`s; `spiffe-helper` sidecar; edge nested/federated topologies + offline survival. _Opt-in_ via `signing_mode: spiffe` (NATS/Redis mTLS via the X.509-SVID still to come) | ✅ Implemented |
| **3** — Runtime-evidence Cat-A | Provider-agnostic kernel-event evidence (`execve`/`openat`/`connect`) folded into Cat-A — detects RHACS / Falco / Tetragon (process+file) and NetObserv (network); a bridge normalises events onto NATS.  Opt-in via `governance.runtimeEvidence.enabled` | ✅ Implemented (opt-in) |
| **4** — Hardened Standalone | NKeys + self-signed CA mTLS for Podman mode; no SPIRE/Tetragon dependency | 🔲 Planned |

> **Phase 1 design decision — Cilium is _not_ ACC's prime mechanism.**
> The roadmap item was originally sketched as "Cilium L7 NetworkPolicy",
> but Cilium is not the default CNI in any ACC deploy scenario —
> OpenShift/RHOAI and MicroShift default to OVN-Kubernetes, K3s uses
> Flannel, and standalone has no Kubernetes at all. The ACC operator is
> a namespaced workload and cannot install or replace a cluster CNI.
> Phase 1 therefore ships a **capability-tiered** design: the portable
> **must-have is standard Kubernetes `NetworkPolicy`** (Tier 1, L3/L4),
> which every policy-enforcing CNI honours. FQDN egress (Tier 2) is
> satisfied by OVN-Kubernetes `EgressFirewall` _or_ Cilium; full L7
> (Tier 3) is the _only_ tier that requires Cilium. The operator emits
> the highest tier the running cluster can enforce — Cilium is an
> optional enhancement backend, never a prerequisite. ACC consumes
> eBPF-backed policy engines; it does not write its own eBPF.
> See [`docs/network-policy.md`](docs/network-policy.md).

Quick setup for the implemented phases:
```bash
# Phase 0a — Ed25519 arbiter verify key (the default trust model)
export ACC_ARBITER_VERIFY_KEY=<base64-encoded-raw-32-byte-ed25519-public-key>

# Phase 0b — Redis auth
export ACC_REDIS_URL=redis://localhost:6379
export ACC_REDIS_PASSWORD=$(openssl rand -hex 32)

# Phase 2 — SPIFFE workload identity (opt-in; requires SPIRE in-cluster)
export ACC_SIGNING_MODE=spiffe
export ACC_SPIFFE_ENABLED=true
export ACC_SPIFFE_TRUST_DOMAIN=acc-prod.example.com

# Phase 0c — NATS NKey authentication (opt-in)
./scripts/acc-nkeys generate --out-dir ./nkeys     # standalone
export ACC_NKEY_ENABLED=true
export ACC_NKEY_SEED_PATH=./nkeys/seed-arbiter     # per-process role seed
```

```yaml
# Phase 1 — L7 / eBPF NetworkPolicy (opt-in; operator-managed, edge/rhoai)
# Phase 3 — Runtime-evidence Cat-A (opt-in; operator-managed, rhoai/edge)
# Both are set on the AgentCorpus CR — the operator emits the objects:
spec:
  networkPolicy:
    enabled: true
    maxTier: 1            # 1 = L4 floor; 2 = FQDN egress; 3 = Cilium L7
    mode: enforce         # use "audit" to canary without dropping traffic
  governance:
    runtimeEvidence:
      enabled: true
      enforce: false      # observe baseline; flip true after the observe window
      preferredBackend: auto   # auto = RHACS > Falco > Tetragon
```

See [`docs/spiffe.md`](docs/spiffe.md) (+ [`docs/spiffe-edge.md`](docs/spiffe-edge.md) for edge topologies) for the SPIFFE setup, the three-stage `ed25519 → spiffe` migration, and the v0.5.0 default-flip plan.  See [`docs/nats-nkeys.md`](docs/nats-nkeys.md) for the NATS NKey setup (per-role identities, the permission matrix, the three deploy modes).  See [`docs/network-policy.md`](docs/network-policy.md) for the capability-tiered network isolation (the four deploy scenarios, the rollout procedure).  See [`docs/runtime-evidence.md`](docs/runtime-evidence.md) for the runtime-evidence Cat-A setup (the RHACS/Falco/Tetragon/NetObserv backends, the observe→enforce rollout).  See [`docs/security-hardening.md`](docs/security-hardening.md) for the complete security architecture, governance layer (Cat-A/B/C Rego rules), and phase-by-phase implementation plan.

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
| `ACC_SIGNING_MODE` | `security.signing_mode` | `auto` → `ed25519` | ROLE_UPDATE signing model: `ed25519` \| `spiffe` |
| `ACC_SPIFFE_ENABLED` | `security.spiffe.enabled` | `false` | Master switch for SPIFFE workload identity |
| `ACC_SPIFFE_TRUST_DOMAIN` | `security.spiffe.trust_domain` | _(empty)_ | SPIFFE trust domain |
| `ACC_NKEY_ENABLED` | `security.nkey.enabled` | `false` | Master switch for NATS NKey authentication |
| `ACC_NKEY_SEED_PATH` | `security.nkey.seed_path` | `/run/acc/nkeys/seed` | Path to this process's NKey seed file |
| `ACC_NKEY_ROLE` | `security.nkey.role` | _(empty)_ | NKey identity (defaults to `agent.role`) |
| `ACC_REDIS_URL` | `working_memory.url` | _(empty)_ | Redis connection URL |
| `ACC_REDIS_PASSWORD` | `working_memory.password` | _(empty)_ | Redis password |
| `ACC_ROLE_SOURCE` | `role_sync.role_source` | `auto` | Role-definition source of truth: `files` \| `crd` \| `mirror` |
| `ACC_PEER_COLLECTIVES` | `agent.peer_collectives` | _(empty)_ | Comma-separated delegation targets |
| `ACC_HUB_COLLECTIVE_ID` | `agent.hub_collective_id` | _(empty)_ | Hub collective ID (edge) |
| `ACC_BRIDGE_ENABLED` | `agent.bridge_enabled` | `false` | Enable cross-collective delegation |

The full SPIFFE / edge / role-sync env-var sets (`ACC_SPIFFE_*`, `ACC_ROLE_SYNC_*`) are documented in [`docs/spiffe.md`](docs/spiffe.md), [`docs/spiffe-edge.md`](docs/spiffe-edge.md), and [`docs/role-sync.md`](docs/role-sync.md).

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
| [`docs/howto-build-deploy-infuse.md`](docs/howto-build-deploy-infuse.md) | **Start here** — the full role lifecycle (build → publish → deploy → infuse → verify) across the CLI, TUI, and WebGUI, with a worked connected/keypair-signed deploy |
| [`docs/howto-standalone.md`](docs/howto-standalone.md) | Podman Compose setup, env vars, Redis auth, Ed25519 keys |
| [`docs/howto-edge.md`](docs/howto-edge.md) | Edge node setup, NATS leaf topology, bridge delegation, offline operation |
| [`docs/howto-rhoai.md`](docs/howto-rhoai.md) | OpenShift operator install, CRD reference, GPU inference, KEDA/Gatekeeper/OTel |
| [`docs/howto-role-infusion.md`](docs/howto-role-infusion.md) | Role definition schema, 4-tier load order, ROLE_UPDATE hot-reload, Ed25519 signing |
| [`docs/CONTRIBUTING-ROLE.md`](docs/CONTRIBUTING-ROLE.md) | Publish your own role package: `acc-pkg init` → evals → keyless cosign → publish; package layout, tiers |
| [`docs/MIGRATING-FROM-INTREE.md`](docs/MIGRATING-FROM-INTREE.md) | Moving from in-tree roles to `@acc/*` packages: `required_packages:`, the dual-source loader, the deprecation cycle |
| [`docs/PUBLISHING-FAMILY-PACKS.md`](docs/PUBLISHING-FAMILY-PACKS.md) | Operator runbook: build, sign, and publish the role packs (incl. the 7 corporate domain packs + umbrella) to `acc-ecosystem` |
| [`docs/howto-tui.md`](docs/howto-tui.md) | Terminal UI: dashboard screen, infuse screen, container deployment, keyboard shortcuts |
| [`docs/webgui.md`](docs/webgui.md) | acc-webgui — the optional FastAPI + React web frontend: architecture, auth tiers, per-mode deployment, the tracing views, the TUI-parity screens |
| [`docs/compliance_governance.md`](docs/compliance_governance.md) | Category A/B/C governance inventory, regulatory frameworks + gap analysis, arbiter rule proposals + the learn-from-violations loop |
| [`docs/multimodel_reviewer.md`](docs/multimodel_reviewer.md) | Per-agent models via `models.yaml`, the reviewer role on a powerful model, the critic loop |
| [`docs/memory_reflection.md`](docs/memory_reflection.md) | Self-reflective memory: the out-of-band consolidation loop, `memory_notes`, the hot-path read |
| [`docs/prompt_caching.md`](docs/prompt_caching.md) | Stable cacheable prefix, per-backend cache hints, Performance-pane cache metrics |
| [`docs/golden_prompts_scheduling.md`](docs/golden_prompts_scheduling.md) | Golden-prompt suite schema, the CLI/TUI runner, the scheduled history runner + cron recipe |
| [`docs/operator-agentset-guide.md`](docs/operator-agentset-guide.md) | Instantiating agentsets via the ACC operator: mapping `collective.yaml` → `AgentCollective` CRD, worked CRs, current CRD gaps |
| [`docs/operator-standalone-parity.md`](docs/operator-standalone-parity.md) | Standalone-vs-operator feature drift, the no-conflict strategy, the tracked parity closers |
| [`docs/spiffe.md`](docs/spiffe.md) | SPIFFE workload identity: prerequisites, config, the `ed25519 → spiffe` migration, v0.5.0 default-flip plan |
| [`docs/spiffe-edge.md`](docs/spiffe-edge.md) | SPIFFE at the edge: nested / federated / ed25519 topologies, offline survival, the compatibility matrix |
| [`docs/nats-nkeys.md`](docs/nats-nkeys.md) | NATS NKey authentication: per-role identities, the publish/subscribe permission matrix, per-deploy-mode setup |
| [`docs/network-policy.md`](docs/network-policy.md) | L7 / eBPF NetworkPolicy: the capability tiers, the four deploy scenarios, the audit→enforce rollout |
| [`docs/runtime-evidence.md`](docs/runtime-evidence.md) | Runtime-evidence Cat-A: the RHACS/Falco/Tetragon/NetObserv backends, the evidence bridge, the observe→enforce rollout |
| [`docs/role-sync.md`](docs/role-sync.md) | Bi-directional `roles/<id>/role.yaml` ↔ `AgentCollective` CRD sync; the three `role_source` modes |
| [`docs/security-hardening.md`](docs/security-hardening.md) | Complete security architecture: Cat-A/B/C Rego rules, Phase 0a–4 implementation plan |
| [`docs/value-proposition.md`](docs/value-proposition.md) | Comparison with LangChain, CrewAI, AutoGen, Haystack |
| [`docs/operator-install-local.md`](docs/operator-install-local.md) | Detailed operator deployment (Kustomize, OLM bundle, CatalogSource) |
| [`docs/operator-certification.md`](docs/operator-certification.md) | Red Hat OperatorHub certification roadmap |
| [`docs/IMPLEMENTATION_SPEC_v0.2.0.md`](docs/IMPLEMENTATION_SPEC_v0.2.0.md) | RHOAI 3 integration design: compatibility matrix, dual-mode pattern |
| [`docs/ACCv3.md`](docs/ACCv3.md) | ACC v3 design paper: sovereign edge-first architecture, biological grounding |

---

## Related repositories

ACC is developed as a small family of repositories:

| Repository | What it is |
|---|---|
| [`flg77/acc`](https://github.com/flg77/acc) | **This repo** — the ACC runtime, operator, TUI/WebGUI, and the `acc-pkg` package toolchain. |
| [`flg77/acc-ecosystem`](https://github.com/flg77/acc-ecosystem) | Public package registry serving the `@acc/*` family packs — discover roles, and publish your own. |
| [`flg77/acc-podman-desktop`](https://github.com/flg77/acc-podman-desktop) | Podman Desktop extension — bring up and govern an ACC collective from the desktop, alongside Podman AI Lab. |
| [`flg77/acc-web-project`](https://github.com/flg77/acc-web-project) | The project website — intro, operations guide, the `/roles` marketplace, and the roadmap (links to this runtime). |

See [**Role & Package Ecosystem**](#role--package-ecosystem) for how they fit together.

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
