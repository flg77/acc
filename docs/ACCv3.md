# Agentic Cell Corpus v3 — Sovereign, Edge-First Agentic Computing with a Datacenter Bridge

**Draft for reviewer circulation**
**Version:** 3.0-draft
**Date:** 2026-04-09
**Author:** Michael
**Supersedes:** *Agent Cell Corpus v2* (design brief, 2026-03), *ACC Specification v1*

---

## Abstract

Agentic AI workloads are rapidly migrating from consumer and edge hardware into centralised datacenters. The proximate driver is not model capability — small models are now competent at many agentic tasks — but the operational weight of the surrounding system: KV-cache pressure, long context windows, vector-store footprint, tool sprawl, and the absence of a runtime that can make a *single* consumer device a trustworthy, self-regulating collaborator inside a larger federated cluster.

This paper proposes the **Agentic Cell Corpus (ACC)**, a biologically-grounded runtime for autonomous agent collectives that (i) is small enough to run on a single Podman pod on consumer hardware, (ii) shares one codebase and one governance model with an enterprise Red Hat OpenShift AI (RHOAI) deployment, and (iii) provides cognitive-level rogue detection and identity-preserving reprogramming that classical Kubernetes observability cannot offer. ACC does not replace Kubernetes, vLLM, or Llama Stack; it layers a minimal, edge-viable homeostatic loop above them, and bridges to them when the local cell connects to the datacenter.

We address six concrete criticisms from reviewer feedback on ACCv2: (1) the lack of a concrete problem statement, (2) the over-broad use of "governance", (3) the absence of a state-of-the-art example, (4) the thin biological→technical mapping, (5) the proximity to Kubernetes, and (6) the specific value of cognitive rogue detection over Kubernetes observability. Every architectural claim in this document is backed by a component already implemented in `agentic-cell-corpus` v0.2.0 (Phase 1a, backend abstraction layer, commit `56a875e`) or by an explicit stub in `IMPLEMENTATION_SPEC_v0.2.0.md`.

---

## 1. Motivation: The Concrete Problem ("So What?")

### 1.1 The drift to the datacenter

Agentic AI today is predominantly executed in the cloud or on datacenter GPUs. The observable trend among production coding agents, multi-agent frameworks and retrieval-heavy assistants is a steady migration of workload *away* from the user's device — not because the models have grown, but because the surrounding runtime has. A 3B-parameter model that happily answers a single prompt on a laptop becomes impractical the moment it is expected to maintain long context, multi-turn memory, vector retrieval, tool use and governance simultaneously. Gopanapalli (2025) documents this as the "agentic scaling squeeze" [^1].

The consequences are:

1. **Cost.** Every reasoning step billed per token.
2. **Sovereignty.** User data leaves the device.
3. **Energy.** A single A100 hour for an intermittent workload is orders of magnitude more energy-intensive than keeping the inference local.
4. **Latency.** Round-trips per reasoning step.
5. **Single points of failure.** A cluster outage suspends every user's coding agent.

### 1.2 The specific problem ACC addresses

**Can a self-regulating agent corpus run entirely on consumer hardware and still contribute meaningful, pre-consolidated work into a larger federated cluster when it chooses to connect?**

Subject to:

- **Constraint C1** — strict memory budget: a single-node agent must fit in ≤8 GB RAM including working memory, embedded vector store, embedded signalling bus, and a local small LLM.
- **Constraint C2** — sovereignty: no reasoning trace, episodic memory or raw input leaves the device without explicit cross-collective authorisation.
- **Constraint C3** — determinism boundary: the cell's externally-visible actions are bounded by declaratively-specified rules even though the reasoning engine is non-deterministic.
- **Constraint C4** — substrate identity: the same codebase, the same governance model and the same signal schema must run unchanged on a Podman pod on a ThinkPad and inside an RHOAI namespace with KServe vLLM, Milvus and a 3-broker Kafka cluster behind it.

These four constraints, taken together, are not met by any existing framework we surveyed (Section 9). ACC is the design we believe can meet them.

### 1.3 A concrete failure mode of the state of the art

Consider the following scenario, run today on an off-the-shelf LangGraph or Llama Stack agent on a laptop with Ollama:

> A developer uses a local coding agent to fix a subtle Rust borrow-checker bug. The fix works. Two days later, the same bug pattern reappears in a different module. The agent re-derives the fix from scratch, spending 40 seconds and ~14k tokens. It has no memory of its own earlier insight because every invocation is a stateless function call.

This is not a model-capability problem — a 3B model was perfectly capable of producing the fix. It is a **runtime architecture problem**: the agent has no persistent episodic memory, no mechanism to consolidate a successful pattern into a reusable rule, and no way to share that pattern with the developer's other machines. The existing frameworks treat the agent as a stateless tool-calling function; they cannot accumulate a persistent behavioural corpus on the user's own hardware.

ACC's answer to this scenario is the ICL → pattern → Category-C rule pipeline (Section 5.2, `cognitive_core.py` steps 6 and 8 in `IMPLEMENTATION_SPEC_v0.2.0.md` §11). The first solution is written to a local LanceDB `episodes` table; after three similar successes the arbiter consolidates them into a pattern; after five it signs a Category-C rule that primes future invocations. All of this happens on the laptop. None of it leaves the device unless the user connects to a shared collective.

---

## 2. Scope and Non-Goals

ACC is a runtime for **autonomous agent collectives** under **bounded agency**. To keep the paper falsifiable, we state what ACC is and is not.

**ACC is:**
- A per-cell control loop that wraps any LLM backend with a three-tier rule engine.
- A signalling schema for inter-cell communication with Ed25519-authenticated identity.
- A memory layer that persists episodes on the edge and bridges them to the datacenter on reconnect.
- A rogue-detection mechanism based on behavioural embedding drift.
- An identity-preserving reprogramming ladder that adjusts a cell's setpoints instead of terminating it.

**ACC is explicitly not:**
- A replacement for Kubernetes, OpenShift, or RHOAI. It uses them.
- A replacement for Llama Stack or vLLM. It uses them as pluggable LLM backends.
- A general "AI governance" framework in the policy/regulation sense. It is runtime-level behavioural control. Regulatory and legal compliance layers sit *above* ACC.
- A training framework. There is no gradient descent inside ACC. Adaptation is via in-context learning (ICL) and rule consolidation only.
- A consensus protocol. The arbiter is a single logical role per collective; multi-arbiter consensus is future work.

---

## 3. Scoping the Term "Governance"

Reviewer feedback correctly flagged that "AI governance" is too broad. We restrict the term as follows.

**ACC governance** means: *the per-cell behavioural control loop that constrains inbound signals, pre-reasoning decisions, post-reasoning outputs and outbound signals, according to a three-tier hierarchy of rules inherited from the collective to which the cell currently belongs.*

It does **not** mean ethical AI policy, regulatory compliance, data privacy law, or content moderation. Those live above ACC and produce inputs to its rule bundles.

### 3.1 The three-tier model

| Tier | Name | Mutability | Enforcement point | Implemented in |
|---|---|---|---|---|
| A | Constitutional | Immutable (WASM, no runtime update) | Agent membrane + (RHOAI) OPA Gatekeeper ConstraintTemplate | `regulatory_layer/category_a/constitutional_rhoai.rego` A‑001…A‑010 |
| B | Conditional setpoints | Live-updatable by arbiter | OPA sidecar per cell | `regulatory_layer/category_b/conditional_rhoai.rego` B‑001…B‑008 |
| C | Adaptive learned | Auto-generated, arbiter-signed, confidence ≥ 0.80 | OPA sidecar per cell | `regulatory_layer/category_c/adaptive_rhoai.rego` |

The hierarchy is strictly ordered: a Category-C rule that conflicts with a Category-A rule is rejected by the membrane at load time. This ordering gives ACC a deterministic envelope around a non-deterministic reasoning engine.

### 3.2 Policy induction under authorisation

A cell joining a collective does not blindly inherit rules. The arbiter of the target collective presents a signed rule bundle; the joining cell's membrane verifies the Ed25519 signature and validates the bundle against its own Category-A constitution before accepting the Category-B/C tiers. This lets policy propagate through a cluster of consumer devices without requiring a central registry, while still preventing a rogue arbiter from installing hostile rules on a federated cell. Full protocol: `IMPLEMENTATION_SPEC_v0.2.0.md` §6.3.

---

## 4. The Biological Mapping (With Substance)

The reviewer's core criticism of ACCv2 was that the biological analogy was decorative. In ACCv3 the mapping is required to be *falsifiable*: for every biological claim there must be a measurable code-level artefact in `agentic-cell-corpus`.

### 4.1 Why biology at all

The argument is pragmatic, not poetic. Biological tissues achieve something agentic AI systems conspicuously do not: minimal external orchestration, graceful degradation, cell-level autonomy, and energy efficiency multiple orders of magnitude beyond silicon. Michael Levin's work on bioelectric signalling, cellular competence and substrate-independent goal-directedness [^2][^3] argues that these properties are not chemistry-specific — they arise from a small set of design patterns that can, in principle, be transferred to a different substrate. ACC is an empirical test of that claim in an agentic runtime.

The driver is **energy efficiency under a strict memory budget** (Constraint C1). If ACC's design cannot produce a per-cell runtime smaller than ~500 MB resident working set and ≤2 GB with a small LLM loaded, it has failed regardless of whether its biology analogy is elegant.

### 4.2 The mapping table

Every row has a concrete file or interface it is implemented against.

| Biological concept (Levin) | ACC artefact | Where in the code | Falsification criterion |
|---|---|---|---|
| Gap junctions (cell-to-cell ionic coupling) | NATS subject hierarchy `acc.{cid}.>` | `acc/backends/signaling_nats.py` | Sub-millisecond local delivery, subject wildcards work across pods |
| Cell membrane (selective permeability) | OPA sidecar + Ed25519 signature verification | `acc/membrane.py`, spec §6 | Every inbound and outbound signal evaluated; invalid signatures dropped |
| Gene regulatory network (input→behaviour) | Pluggable LLM backend interface | `acc/backends/llm_*.py` | Backend swap (`ollama`→`vllm`) leaves cognitive core behaviour structurally identical |
| Epigenetic memory (experience alters expression) | LanceDB `episodes`/`patterns`/`collective_mem` tables | spec §8.2, `acc/memory.py` (Phase 1b) | Repeated task types increase ICL hit rate over time |
| Homeostatic sensing (setpoint deviation) | `health_score`, `drift_score` in STATE_BROADCAST | spec §11 step 8 | Cell self-reports DEGRADED within one heartbeat interval of crossing setpoint |
| Apoptosis (programmed cell death) | KEDA graceful scale-down with final STATE_BROADCAST and LanceDB flush | spec §10.4 | Scaled-down cell's episodes are still retrievable by replacement cell |
| Morphogenetic signalling (tissue-level pattern) | Arbiter pattern consolidation → Cat-C rule generation | spec §11, §12.3 | Collective-level Cat-C rule propagates to all member cells within one OPA bundle poll interval (30 s) |
| Cellular competence (local goal-directedness) | Each cell carries full cognitive core + rule set; no central orchestrator required | `acc/agent.py` | A standalone cell accomplishes a single-agent task with arbiter absent |
| Cancer (uncontrolled proliferation, pattern drift) | `drift_score` crossing 0.35 threshold plus missing heartbeat | spec §12.2 B‑003, §9.4 alert `ACCAgentDriftExceeded` | Rogue cell triggers isolation protocol within 60 s without human intervention |

Each row is a testable hypothesis about the ACC runtime, not a metaphor.

---

## 5. Architecture Overview

### 5.1 Two runtime modes, one codebase

The single most important architectural decision in ACCv3 is that the edge and datacenter are not two deployments — they are one codebase with a config-selected backend stack. This is realised by the Protocol-based backend abstraction layer in `acc/backends/__init__.py` (Phase 1a, already implemented).

```
+-----------------------------------------------------------------------+
|  ACC Cell (same binary in both modes)                                 |
|                                                                       |
|   +-----------------+   +-----------------+   +-----------------+     |
|   | Cognitive Core  |<->| Membrane (OPA)  |<->| Identity        |     |
|   | 8-step loop     |   | A / B / C tiers |   | (Ed25519)       |     |
|   +--------+--------+   +-----------------+   +-----------------+     |
|            |                                                          |
|   +--------+--------+ SignalingBackend  LLMBackend  VectorBackend     |
|   | Backend Registry|-------|-----------|-----------|                 |
|   +--------+--------+       |           |           |                 |
|            |                v           v           v                 |
|            |         Standalone mode:                                 |
|            |           NATS (embedded) | Ollama   | LanceDB           |
|            |         RHOAI mode:                                      |
|            |           NATS + Kafka br.| vLLM /   | Milvus            |
|            |                           | LlamaStk |                   |
+-----------------------------------------------------------------------+
         ^                                                      ^
         |                                                      |
    edge (Podman pod,                                    RHOAI (OpenShift,
     1 pod, ≤2 GB RAM)                                   KServe + KEDA + OPA
                                                         Gatekeeper + Milvus)
```

Concretely, the nine files in `acc/backends/` (`signaling_nats.py`, `vector_lancedb.py`, `vector_milvus.py`, `llm_ollama.py`, `llm_vllm.py`, `llm_llama_stack.py`, `llm_anthropic.py`, `metrics_log.py`, `metrics_otel.py`) are all present and conform to four Protocol interfaces: `SignalingBackend`, `VectorBackend`, `LLMBackend`, `MetricsBackend`. The concrete backend is selected from `acc-config.yaml` plus environment variables (spec §4.3, §4.4). No code branching on deploy mode exists in the cognitive core.

### 5.2 The eight-step reconciling autonomy loop

ACC's cognitive core implements a loop that is intentionally small. Every step is measurable and auditable.

```
INBOUND SIGNAL
   |
   v
[1] ROUTE        classify signal_type
[2] RECALL       embed task, retrieve top-k episodes + collective context
[3] PRE-GOVERN   evaluate A/B/C rules BEFORE LLM call
   |------- denied --> ALERT_ESCALATE, skip LLM call
[4] REASON       LLM call: system prompt (identity + rules + state),
                 user prompt (task + episodes), structured JSON output
[5] POST-GOVERN  evaluate A/B/C rules AGAINST LLM output
   |------- violated -> ALERT_ESCALATE, block output
[6] LEARN        write episode (input/output embeddings, outcome, icl_note)
[7] ACT          execute validated decision via MCP tools + outbound signals
[8] FEEDBACK     recompute health_score, drift_score, publish STATE_BROADCAST
                 every 50 episodes: arbiter triggers pattern consolidation
```

Steps 3 and 5 are what distinguish ACC from a stateless tool-calling agent: the LLM is sandwiched between two governance evaluations. Steps 6 and 8 are what distinguish ACC from a microservice: experience measurably alters future behaviour and the cell measurably senses its own deviation from collective norms.

Full diagram: `IMPLEMENTATION_SPEC_v0.2.0.md` §11.

### 5.3 Edge-first, bridge when connected

The standalone mode runs with zero external infrastructure:

- **Signalling:** NATS server embedded in the pod (`deploy/pod.yaml`).
- **Working memory:** Redis sidecar.
- **Episodic memory:** LanceDB, embedded, in-process.
- **LLM:** Ollama with a small model (e.g. `llama3.2:3b`).
- **Governance:** OPA sidecar loading a local ConfigMap bundle.

Total resident footprint target: ≤2 GB with the small LLM loaded, ≤500 MB for the ACC control plane alone. A single `podman play kube deploy/pod.yaml` brings a complete, governed, self-regulating cell online on a laptop with no network dependency.

When the cell reconnects to an RHOAI cluster (the "bridge" event):

1. **Identity handshake.** The cell publishes its existing `agent_id` and generation counter to the collective registry over Ed25519-signed NATS.
2. **Rule reconciliation.** The collective's OPA bundle server sends the current Category-B/C bundle; the cell's membrane verifies the signature and validates it against its own Category-A constitution before loading.
3. **Memory sync.** The cell's LanceDB episodes with `created_at_ms > last_sync_ms` are published as batched `SYNC_MEMORY` signals. A Milvus sync worker deduplicates by `record_id` and inserts (spec §8.3). Last-write-wins on conflict.
4. **Signal bridging.** All internal NATS traffic continues to use NATS for sub-ms latency; a NATS→Kafka bridge (spec §5) translates MessagePack to JSON, injects W3C traceparent headers, and publishes to `acc.audit.all` / `acc.signals.*` / `acc.events.*` for enterprise consumers (Tempo, Elasticsearch, Grafana, AlertManager).

The bridge is one-way for internal signals (NATS is authoritative) and two-way for task assignments (the enterprise layer can inject tasks via Kafka → Bridge → NATS). This protects local latency guarantees while giving the datacenter a complete audit trail and enterprise integration surface.

---

## 6. Why ACC Is Not "Kubernetes for Agents"

Reviewer feedback rightly observed that ACC's operational concepts resemble Kubernetes primitives: liveness probes (heartbeat), readiness (drift score), horizontal autoscaling (KEDA), admission webhooks (OPA Gatekeeper). The overlap is real and intentional — ACC happily inherits every stable pattern Kubernetes already provides. The question is what ACC adds that cannot emerge by treating agents as microservices. Three specific gaps.

### 6.1 The non-determinism boundary

Classical microservices produce deterministic outputs given identical inputs. A liveness probe can therefore validate "this service is healthy" from a single HTTP response. A generative LLM-driven agent produces a *distribution* of outputs, and the distribution can silently shift. A pod can return HTTP 200 on every probe while its cognitive output is catastrophically off-policy.

ACC's response is the pre- and post-governance sandwich around the LLM call (Section 5.2, steps 3 and 5). Neither of these is a microservice concept; both require OPA evaluation of the structured LLM output against the cell's rule bundle *before the output is allowed to leave the membrane*. Liveness cannot do this.

### 6.2 Identity preservation across reprogramming

In the pets-and-cattle mental model, a misbehaving pod is killed and replaced. This works because microservices are assumed stateless or externally-stated. It is the wrong abstraction for a generative cell that has accumulated hundreds of ICL episodes and a learned Category-C rule set: killing the pod discards the corpus.

ACC introduces the five-level intervention ladder (Levin-derived, `IMPLEMENTATION_SPEC_v0.2.0.md` §1 and §14):

1. **Setpoint adjustment** — arbiter updates Category-B data (e.g. raise `max_task_duration_ms`).
2. **Targeted rule injection** — arbiter pushes a new Category-C rule narrowing the cell's behaviour.
3. **Partial memory flush** — cell clears working state but retains episodic memory and identity.
4. **Full reprogramming** — cell replaces its rule bundle but retains `agent_id` and generation counter (generation += 1).
5. **Apoptosis** — terminal case only; graceful shutdown with memory flush to the collective store so a successor can inherit.

Generations are tracked (Category-A rule A‑008 caps generations before mandatory human review, spec §12.1). Identity persists across every level except 5. The analogy is correct: a reprogrammed cell is still the same cell, the same way a liver cell with altered gene expression is still the same cell.

### 6.3 Edge as a first-class runtime

Kubernetes' edge story is thin. ACC's design pressure is to work *without* a control plane: `podman play kube deploy/pod.yaml` on a single laptop is a valid deployment. RHOAI is the federation target, not the substrate. Gatekeeper, KEDA, Service Mesh and KServe are optional enhancements when connected; none of them are required to run a single governed cell.

This asymmetry forces a set of design choices that a datacenter-first framework would not make: NATS instead of Kafka as the internal bus (sub-ms latency, ~60 MB RAM), LanceDB instead of Milvus (embedded, no server), Ollama as a first-class peer to vLLM, OPA as a sidecar rather than a cluster-wide service.

---

## 7. Cognitive Rogue Detection: Why Observability Is Not Enough

Reviewer feedback asked why classical Kubernetes observability (metrics, logs, traces) is insufficient for detecting a "rogue" agent, and what the cancer analogy concretely means.

### 7.1 What classical observability captures

Prometheus, Tempo, Elasticsearch and Grafana capture infrastructure metrics (CPU, RAM, network), application logs, and distributed traces. For a deterministic microservice this is enough: a service that starts returning 500s, consuming 95% CPU, or tracing a suspicious call graph is detectable.

### 7.2 What it cannot capture

For a generatively-driven agent the rogue cases are semantic, not infrastructural:

- **Multi-modal output drift.** An agent begins producing plausible but subtly incorrect analyses. All HTTP responses are 200, all traces are normal, all metrics are green.
- **Reasoning path degradation.** The agent's `thinking` field still passes schema validation but its structure diverges from the collective's learned reasoning patterns.
- **Logprob distribution shift.** The model's output distribution becomes bimodal or flattened in ways that predict hallucination but are invisible to token-level validation.
- **Embedding centroid drift.** The agent's recent outputs cluster away from the collective's centroid in embedding space, indicating it is solving a subtly different problem from its peers.

None of these are computable from Prometheus metrics or OTel spans alone. They require access to the generative output itself, the token logprobs, and an embedding model run against the agent's recent history.

### 7.3 The drift score as a cognitive vital sign

ACC introduces `drift_score` as a first-class signal. It is computed at step 8 of the cognitive loop as the cosine distance between the cell's recent output-embedding centroid and the collective's centroid, normalised to [0, 1]. The threshold of 0.35 (`IMPLEMENTATION_SPEC_v0.2.0.md` §12.2 B‑003) is empirically chosen as the point beyond which the cell's outputs predictably diverge from the collective's task distribution. A cell that crosses the threshold:

1. Self-reports DEGRADED in its next STATE_BROADCAST.
2. Is blocked from participating in collective consensus (Category-B rule B‑003, `allow_collective_participation`).
3. Triggers the Prometheus `ACCAgentDriftExceeded` alert (spec §9.4).
4. Is a candidate for the intervention ladder (Section 6.2). Level 1 first; apoptosis last.

The cancer analogy is precise in exactly this respect: a cell whose behaviour has drifted from collective norms while still passing every infrastructure-level health check is the AI equivalent of a cell proliferating off-pattern while returning normal vital signs. Detecting it requires a cognitive-level probe, not an infrastructure-level one. Emitting the drift signal into OTel and Prometheus (spec §9.2) then lets *existing* observability tooling alert on it, but the signal itself is necessarily ACC-native.

### 7.4 Composition, not competition

ACC's governance and observability explicitly layer over Kubernetes-native observability. OTel spans are emitted for every governance decision, LLM call and signal publication. Prometheus recording rules turn ACC signals into dashboardable metrics. AlertManager routes ACC-specific alerts (`ACCAgentDegraded`, `ACCAgentDriftExceeded`, `ACCAgentHeartbeatMissing`, `ACCCollectiveIsolationEvent`) into the enterprise's existing incident response. The enterprise inherits ACC's semantic signals *through* its existing observability stack rather than learning a new one.

---

## 8. Consumer Hardware as a First-Class Contributor

### 8.1 Pros

- **Cost.** No per-token billing for routine work. LLM inference is paid for once (hardware) rather than per invocation.
- **Sovereignty.** Raw inputs, reasoning traces and episodic memory remain local by default. Federation happens over Ed25519-signed explicit handshakes.
- **Energy.** A ~15 W laptop inference loop for intermittent work is orders of magnitude more efficient than a fractional A100-hour. For household-scale workloads ACC is the energy-winning architecture.
- **Federation.** Each consumer device is a potential node in a larger collective. Pre-consolidated partial results (episodes, patterns, Category-C rules) can be contributed upstream when the user opts in.
- **Resilience.** A cluster outage suspends every cloud agent. A device outage suspends only one ACC cell.
- **Model sovereignty.** The user chooses the local model. Ollama, llama.cpp, MLX, ROCm — ACC is indifferent at the backend interface.

### 8.2 Cons (and how ACC addresses them)

- **Memory pressure.** Hard constraint. Mitigations: embedded stores (LanceDB, embedded NATS, OPA sidecar), strict `max_task_queue_depth` setpoint (B‑series rules), ICL episode eviction after consolidation.
- **Storage.** Episodic memory grows unbounded without pattern consolidation. Mitigation: arbiter-triggered consolidation every 50 episodes (step 8), compression of episodes into patterns, demotion of stale patterns to cold storage.
- **Regulatory compliance.** A consumer cell joining a corporate collective must meet the enterprise's compliance bar. Mitigation: Category-A rules pinned to the collective's constitutional baseline; Gatekeeper ConstraintTemplates at the namespace boundary (spec §6.2); mTLS and managed RBAC at the cluster layer.
- **Cold start.** A scale-to-zero collective reactivates slowly. Mitigation: arbiter always warm (`minReplicaCount: 1`, spec §10.3); episodic memory persists through hibernation; cells rehydrate from LanceDB/Milvus on start.
- **Model quality.** Small models produce worse raw reasoning than frontier models. Mitigation: ACC's ICL+governance loop is designed to lift small-model reliability via pattern re-use and hard rule enforcement. This is a testable hypothesis and a target for the Phase 4 evaluation (spec §15.1).

### 8.3 The federated-learning-adjacent angle

ACC's memory sync protocol (spec §8.3) is structurally similar to federated learning: cells compute locally and share only consolidated derivatives (episodes, patterns, Category-C rule candidates) with the collective. Unlike federated learning there is no gradient aggregation — ACC does not train the underlying model. But the sovereignty and network-efficiency properties carry over: the user's raw data never leaves the device; only signed, aggregated behavioural derivatives do.

---

## 9. Related Work and State of the Art

A short survey of the landscape ACC occupies, and where it fits.

**Llama Stack (Meta / Red Hat).** Production-grade agent orchestration API with MCP tool integration, safety shields (Llama Guard), RAG and MLflow Tracing. Stateless request/response model; no persistent agent identity, no inter-agent memory transfer, no drift detection. ACC treats Llama Stack as a high-quality LLM backend (`acc/backends/llm_llama_stack.py`) and layers governance and homeostasis above it.

**Llama Stack + KServe / vLLM (RHOAI 3).** Red Hat's enterprise stack for model serving, pipelines, observability and agent orchestration. Datacenter-first. ACC targets RHOAI 3 as its federation substrate (`ACC_DEPLOY_MODE=rhoai`) but remains viable without it.

**LangGraph, CrewAI, AutoGen.** Multi-agent orchestration frameworks. Strong on flow composition and role specification; weak on persistent state, drift detection and identity across sessions. They occupy the orchestration layer that would sit either above or beside ACC cells.

**Kubernetes operators for AI (KAgent, etc.).** Treat agents as pods. Inherit the pets-and-cattle assumption and therefore do not preserve agent identity across reprogramming. ACC's five-level intervention ladder is the explicit alternative.

**Federated learning frameworks (Flower, FedML).** Address the sovereignty and network properties but only for training. ACC addresses them for *runtime* agent collectives, which is an adjacent problem with different primitives (no gradients, yes in-context episodes).

**Michael Levin's bioelectric / competency research [^2][^3].** The theoretical grounding for ACC's design principles. Levin's argument that goal-directedness is substrate-independent and that cellular competence can be evoked in non-biological substrates is what makes ACC a coherent research programme rather than an analogy.

**Gopanapalli, "Agentic AI Scaling: Why your data belongs in the cloud" [^1].** The industry position ACC is arguing *against*. The paper documents the pull into the datacenter; ACC argues the pull is an artefact of missing runtime abstractions, not a law of physics.

ACC's position in this landscape: it is the missing **runtime layer** between a local model and an orchestration framework, with explicit support for both edge-standalone and datacenter-federated operation from a single codebase.

---

## 10. Current Implementation Status

Working against `IMPLEMENTATION_SPEC_v0.2.0.md` and the repository at commit `56a875e` (Phase 1a merged to `feature/ACC-1-phase1a-backend-abstraction-layer`):

**Implemented (v0.2.0 Phase 1a, merged).**
- `acc/backends/__init__.py` — four Protocol interfaces (`SignalingBackend`, `VectorBackend`, `LLMBackend`, `MetricsBackend`).
- All nine backend stubs: `signaling_nats.py`, `vector_lancedb.py`, `vector_milvus.py`, `llm_ollama.py`, `llm_vllm.py`, `llm_llama_stack.py`, `llm_anthropic.py`, `metrics_log.py`, `metrics_otel.py`.
- `acc/config.py` — `acc-config.yaml` loader with env-var overrides.
- `acc/agent.py` — skeleton cell entry point.
- `rules/category_{a,b,c}/` — v0.1.0 Rego rule templates.
- `regulatory_layer/` — v0.2.0 RHOAI-enhanced Rego templates (Cat A A‑001…A‑010, Cat B B‑001…B‑008, Cat C examples).
- `deploy/pod.yaml` — standalone Podman manifest.

**Planned for Phase 1b (cognitive core).**
- `acc/cognitive_core.py` — the 8-step loop.
- `acc/membrane.py`, `acc/identity.py`, `acc/memory.py`, `acc/homeostasis.py`, `acc/signaling.py` as full implementations.
- Role-specific modules under `acc/roles/` (ingester, analyst, synthesizer, arbiter).
- Three MCP servers under `acc/mcp/` (skills, resources, tools/signal-router).

**Planned for Phase 2a–2c (RHOAI deployment).**
- Full `deploy/rhoai/` manifest tree: KServe InferenceServices, KEDA ScaledObjects, OPA bundle server, Gatekeeper ConstraintTemplates, Prometheus AlertManager rules, Grafana dashboard, NATS-Kafka bridge.

**Planned for Phase 4 (validation).**
- Scenario 11: three-agent document processing pipeline end-to-end on both standalone and RHOAI modes, demonstrating identical behaviour under the backend abstraction.
- Edge-to-datacenter sync test.
- Scale-to-zero and reactivation test.
- Rogue agent drift detection test under KEDA scaling.

The important property for reviewers: every claim in Sections 3–7 of this paper maps to either an already-implemented component or a concretely-specified stub in `IMPLEMENTATION_SPEC_v0.2.0.md` §4–§12. Nothing in this paper is hand-waved beyond the specification.

---

## 11. Open Questions and Next Steps

These are the genuinely open questions we would like reviewer input on.

1. **Drift-score threshold calibration.** The 0.35 threshold is a placeholder. How do we empirically calibrate it per task-type without introducing per-task model training?
2. **Arbiter-as-single-logical-role.** One arbiter per collective is a centralisation risk. Is a Raft-style multi-arbiter quorum worth the complexity, or does the apoptosis-and-rebirth pattern handle arbiter failure adequately at edge scale?
3. **Category-C rule hygiene.** Auto-generated rules at 0.80 confidence can still compound errors over generations. Should Category-C rules decay without reinforcement, or require periodic re-validation against fresh episodes?
4. **Cross-collective federation.** Category-A rule A‑010 blocks cross-collective signalling unless `via_bridge == true`. What does the bridge protocol look like formally, and what trust model does it imply?
5. **Llama Guard vs Category A overlap.** Llama Stack's safety shields and ACC's Category-A rules both constrain LLM outputs. What is the principled division of labour, and how do we avoid double-enforcement costs?
6. **Edge Milvus.** Milvus Lite could in principle replace LanceDB on the edge, unifying the vector-DB story. Is the footprint cost acceptable under Constraint C1?
7. **Evaluation methodology.** How do we construct a benchmark that fairly measures "a self-regulating cell on a laptop contributing meaningful work to a cluster" without reducing it to either raw throughput or raw quality?

---

## 12. Conclusion

ACCv3 is a bet on three claims:

1. **Agentic workloads belong partly on consumer hardware.** The current migration to the datacenter is a symptom of a missing runtime abstraction, not a law of scaling.
2. **A minimal biologically-grounded control loop — membrane, gene regulatory network, epigenetic memory, homeostatic sensing, morphogenetic signalling — is the right abstraction for autonomous agent cells.** Each element maps to a falsifiable code artefact.
3. **Edge and datacenter should share one codebase and one governance model.** The same ACC cell runs on a Podman pod on a laptop and inside a RHOAI namespace, selected by configuration, with full audit trails, KEDA autoscaling, KServe vLLM inference and a NATS-Kafka bridge when federated.

None of these claims require new models or new infrastructure. They require a specific, small runtime layer between the model and the orchestration framework. That runtime is ACC. Phase 1a is in the repository; Phase 1b and beyond follow the dependency graph in `IMPLEMENTATION_SPEC_v0.2.0.md` §15.1.

We welcome review, in particular on the seven open questions in Section 11 and on any architectural claim that is not adequately backed by a code-level artefact.

---

## References

[^1]: Gopanapalli, M. (2025). *Agentic AI Scaling: Why Your Data Belongs in the Cloud.* Substack. https://manojgopanapalli.substack.com/p/agentic-ai-scaling-why-your-data

[^2]: Levin, M. (2019). The Computational Boundary of a "Self": Developmental Bioelectricity Drives Multicellularity and Scale-Free Cognition. *Frontiers in Psychology* 10:2688.

[^3]: Levin, M. (2022). Technological Approach to Mind Everywhere: An Experimentally-Grounded Framework for Understanding Diverse Bodies and Minds. *Frontiers in Systems Neuroscience* 16:768201.

[^4]: Red Hat. *Red Hat OpenShift AI 3 Documentation.* https://docs.redhat.com/en/documentation/red_hat_openshift_ai

[^5]: Meta / llama-stack. *Llama Stack: Building Blocks for Generative AI Applications.* https://github.com/meta-llama/llama-stack

[^6]: KEDA: Kubernetes Event-driven Autoscaling. https://keda.sh

[^7]: KServe. *Standardized Serverless ML Inference Platform on Kubernetes.* https://kserve.github.io/website/

[^8]: Open Policy Agent (OPA) and Gatekeeper. https://open-policy-agent.github.io/gatekeeper/

[^9]: NATS JetStream. https://docs.nats.io/nats-concepts/jetstream

[^10]: LanceDB. *Serverless Vector Database.* https://lancedb.com

[^11]: Milvus. https://milvus.io

[^12]: vLLM. *Easy, Fast, and Cheap LLM Serving.* https://github.com/vllm-project/vllm

[^13]: Michael, M. (2026). *IMPLEMENTATION_SPEC_v0.2.0: ACC Integration with Red Hat AI Enterprise 3.* Internal design document, `agentic-cell-corpus/docs/IMPLEMENTATION_SPEC_v0.2.0.md`.

---

*Draft v3.0 — 2026-04-09 — prepared for reviewer circulation. Comments to the author.*
