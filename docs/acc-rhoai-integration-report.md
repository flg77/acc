# ACC ↔ Red Hat OpenShift AI 3.4 + upstream Kagenti

*Drafted 2026-05-14.  Operator-local; gitignored.  Source artefact
for proposal slots 011–016 in the Obsidian vault.*

## Executive summary

Red Hat OpenShift AI **3.4** was unveiled at Red Hat Summit on
2026-05-12 and is in **EA2** as of this writing; GA is expected
late May.  The release reframes RHOAI from "MLOps" to "**AgentOps**"
and lands three primitives that materially change ACC's
integration story:

1. **Llama Stack Operator** — promoted with the new
   `LlamaStackDistribution` CR.  Exposes OpenAI-compatible
   Responses + Agents API on KServe.
2. **SPIFFE/SPIRE workload identity** — short-lived tokens
   replacing static keys.  Directly validates ACC's deferred
   **proposal 004** (Spiffe role identity).
3. **AMQ Streams (Kafka)** is the strategic event bus; NATS
   remains unrecognised in 3.4 materials.  llm-d adds distributed
   inference with speculative-decoding GA.

Upstream **Kagenti** (`github.com/kagenti/kagenti`, Red Hat-led, Apache 2.0)
is planned as part of Red Hat AI in **2H 2026**.  It ships a
single-agent `Component` CRD + an `AgentCard` discovery CRD, plus
SPIFFE-based zero-trust mTLS via Istio Ambient + Keycloak token
exchange.  No collective semantics; no role taxonomy; no Kafka/NATS.

**Bottom line for ACC.**  Our existing rhoai integration is
~80% there: Milvus abstraction ✅, vLLM/Llama Stack backends ✅,
KServe InferenceService refs ✅, Kafka *audit* bridge ✅.  The
gaps are concentrated in **identity** (SPIFFE), **upstream
discovery** (Kagenti `AgentCard`), and **signaling portability**
(Kafka for inter-agent signals, not just audit).  Six concrete
proposals follow.

---

## 1. Current ACC ↔ RHOAI surface (audit findings)

| Surface | Status | Evidence |
|---|---|---|
| Config validation (`deploy_mode: rhoai`) | ✅ | `acc/config.py` L481-490 requires `vector_db.milvus_uri` + LLM URL |
| Vector DB abstraction | ✅ | `acc/backends/vector_milvus.py` — pure config, no leaky URLs |
| LLM backends | ✅ | `vllm`, `llama_stack`, `openai_compat`, `anthropic` — all RHOAI-friendly via configurable `base_url` |
| KServe `InferenceService` ref | ✅ | `operator/api/v1alpha1/agentcollective_types.go` L170-195 (LLMSpec.VLLM) |
| Kafka audit bridge | ✅ | `acc/audit.py` L159-242 (KafkaAuditBackend) + offline Redis queue fallback |
| RHOAI / KServe prerequisite probe | ✅ | `operator/internal/reconcilers/prerequisites.go` detects RHOAI + KServe + KEDA |
| Cat-A WASM policy | ✅ | `regulatory_layer/category_a/constitutional_rhoai.rego` compiled to WASM |
| Role-sync (CRD ↔ files) | ✅ | proposal 010 just landed; `role_source: crd` is the rhoai default |
| **Signaling on Kafka** | ❌ | `acc/config.py` L35: `SignalingBackendChoice = Literal["nats"]` — NATS only |
| **SPIFFE workload identity** | ❌ | nothing in `acc/security.py`; CR identity uses plain `role.id` (deferred slot 004) |
| **TrustyAI Guardrails integration** | ❌ | No hooks into TrustyAI's input/output detectors; our Cat-B is local-only |
| **Garak red-teaming** | ❌ | No eval-time adversarial scan; eval pipeline runs only the rubric YAML |
| **Llama Stack `Agents` API consumer** | ⚠️ partial | We *expose* OpenAI-compat via vLLM but don't *consume* Llama Stack's Agents/Responses API surface |
| **Kagenti `AgentCard` emission** | ❌ | No `acc.io/role-sync-source`-style annotation aimed at Kagenti's signature scheme |
| **MaaS gateway client** | ❌ | No quota/policy negotiation with RHOAI's new Model-as-a-Service gateway |
| **llm-d distributed inference hint** | ❌ | We treat every LLM endpoint as a single backend; no speculative-decode / fleet awareness |

Coverage gaps in tests (no envtest harness for reconcilers, no
Milvus connectivity probe test, no KServe `InferenceService`
creation test) are pre-existing and tracked separately.

---

## 2. Kagenti — what it is and what it isn't

Upstream Kagenti's stated mission is **"open-source platform for
deploying, securing, and governing AI agents"**.  Red Hat-led,
Apache 2.0, planned as part of Red Hat AI 2H 2026.  Current state
(May 2026): `kagenti/kagenti` v0.5.1, `kagenti-operator`
v0.2.0-rc.4.

What it ships:

- **`Component` CRD** — a single agent workload + framework binding
  (LangGraph / CrewAI / AG2 / BeeAI run as-is).
- **`AgentCard` CRD** — discovery + signature verification metadata,
  modelled on A2A's `/.well-known/agent-card.json`.
- **NetworkPolicy controller** — derives NetPols from
  `AgentCard` verification outcomes.
- **Sidecar injection** — `spiffe-helper`, `kagenti-client-registration`
  auto-injected on pods labeled `kagenti.io/type: agent`.
- **MCP Gateway** — registers tool endpoints as
  `MCPServerRegistration` resources.

What it explicitly **lacks** (compared to ACC):

- No `AgentCollective`-style multi-agent grouping CRD.
- No role taxonomy (ingester / analyst / arbiter / observer /
  coding_agent).  Roles are app-layer conventions.
- No NATS, no Kafka — synchronous A2A JSON-RPC over HTTPS only.
- No Cat-A/B/C governance classification.  Governance is
  identity-focused (SPIFFE + Keycloak), not classification-tiered.
- No bi-directional sync between filesystem authoring and CRDs;
  `AgentCard` reconciliation is workload → CR one-way.

**Conclusion**: Kagenti and ACC are complementary, not competitive.
Kagenti owns the *single-agent zero-trust deployment* primitive;
ACC owns the *collective coordination + governance taxonomy*
above it.

---

## 3. RHOAI 3.4 — what to align with

Confirmed primitives we should aim at:

| Primitive | RHOAI 3.4 piece | ACC alignment opportunity |
|---|---|---|
| Llama Stack Operator | `LlamaStackDistribution` CR | Consume the OpenAI-compat endpoint it exposes (already supported); optionally emit a `LlamaStackDistribution` from our operator when `deploy_mode: rhoai` + LLM backend is `llama_stack` |
| vLLM + llm-d | `vllm-cuda-rhel9` + new llm-d distributed inference | We already use the same vLLM image; add a "fleet-aware" flag for coding_agent's parallel-task pattern |
| KServe `InferenceService` | unchanged | Already referenced in our CRD |
| SPIFFE identity | new `GatewayConfig` CR + token-exchange via Keycloak | Promote proposal 004 from deferred — `spiffe_id` field on role definitions |
| Model-as-a-Service | Governed OpenAI-compat gateway with quota + policy | Add MaaS client to our LLM backend factory |
| TrustyAI Guardrails Orchestrator | Inline input/output detectors | Hook from Cat-B governance layer |
| Garak adversarial scan | Llama Stack evaluation provider | Add as an optional eval-time stage on `coding_agent` outputs |
| OpenTelemetry + Tempo | Default observability | Already use OTel; document the Tempo/Prometheus ingest path |
| AMQ Streams (Kafka) | Strategic durable bus | Add Kafka **signaling** backend (today we only have Kafka **audit** backend) |

Crucially: RHOAI 3.4 has **no first-class `Agent` CRD**.  Our
`AgentCorpus` + `AgentCollective` don't collide with anything Red
Hat ships today.  Llama Stack Operator manages distributions, not
agent identity or workflow lifecycle — those are still
customer-owned in 3.x, which is where ACC sits naturally.

---

## 4. Six proposals (slots 011–016)

Numbered for the Obsidian vault.  Each ~120 words.

### Slot 011 — SPIFFE workload identity (promote proposal 004)

RHOAI 3.4 ships SPIFFE/SPIRE-issued short-lived tokens as the
AgentOps identity model.  This validates the deferred proposal
004's Spiffe-style `spiffe://<trust-domain>/role/<id>` thread.

Promote 004 from deferred → active.  Scope: add `spiffe_id` (computed
from `metadata.name` + trust-domain) to `AgentCollective` status;
wire workload identity into the agent pod's outbound HTTPS via
`spiffe-helper` sidecar (Kagenti already does this — we adopt the
pattern).  Replaces our current Ed25519 verify-key model for
agent-to-arbiter ROLE_UPDATE signing on rhoai.

Effort: L (touches CRD schema, operator reconciler, agent bootstrap).
Risk: medium — coexists with the existing `arbiter_verify_key`
config for a deprecation window.

### Slot 012 — `AgentCard` emission (Kagenti adoption)

When `deploy_mode: rhoai`, the operator emits a Kagenti
`AgentCard` resource per `AgentCollective` member.  This unlocks
Kagenti's signature verification + NetworkPolicy controller +
A2A `/.well-known/agent-card.json` discovery without ACC
reinventing them.

Scope: new `operator/internal/reconcilers/kagenti/` package; honours
the same `targetRef` pattern Kagenti's operator uses.  Optional —
gated by detecting the `agentcard.kagenti.io` CRD.

Effort: M.  Risk: low (additive; doesn't change ACC's own
internals).

### Slot 013 — Kafka signaling backend (rhoai durability)

Today our Kafka usage is audit-only.  RHOAI's strategic event bus
is AMQ Streams; running ACC signals on NATS in a datacenter
deployment means another piece of infrastructure to operate.

Scope: new `acc/backends/signaling_kafka.py` implementing the
existing `SignalingBackend` Protocol.  Behind `signaling.backend:
kafka` flag.  Round-trip semantics identical to NATS via Kafka
topic-per-subject mapping; consumer group per agent.

Effort: M.  Risk: medium (new dep on confluent-kafka,
ordering semantics differ subtly from NATS).

### Slot 014 — TrustyAI Guardrails + Garak integration

RHOAI 3.4's compliance story relies on TrustyAI Guardrails
Orchestrator for inline detection + Garak for adversarial scan.
ACC's Cat-B governance has no hooks into either.

Scope:

1. Cat-B input/output filter calls TrustyAI's
   `chat.completions.create` detection endpoint when configured.
2. Add a `garak_scan` step to the coding_agent eval pipeline,
   triggered when `compliance.frameworks` includes `OWASP_LLM_TOP10`.
3. Surface findings in the audit chain (already Kafka-bridged).

Effort: M.  Risk: low (additive optional integration).

### Slot 015 — Llama Stack `LlamaStackDistribution` consumer

When `deploy_mode: rhoai` and `llm.backend: llama_stack`, our
operator could *create* a `LlamaStackDistribution` CR rather than
expecting one to exist out-of-band.

Scope: extend `LLMSpec.LlamaStack` with `deploy: true` flag (mirror
existing vLLM `Deploy` field at agentcollective_types.go L179).
When set, reconciler creates the LSD CR + waits for ready.
Status field reports the resulting URL back into the
`AgentCollective` status.

Effort: S–M.  Risk: low.

### Slot 016 — llm-d + MaaS awareness for coding_agent fleets

llm-d's speculative-decoding GA in 3.4 is most valuable for the
coding_agent's parallel-task fan-out pattern.  RHOAI's
Model-as-a-Service gateway adds quota + policy negotiation.

Scope:

1. New `llm.fleet_mode: "single" | "llm-d"` config flag — when
   `llm-d`, the OpenAI-compat backend opts into speculative
   decoding hints in request headers.
2. New `llm.maas_client` block with `api_key_env` + quota
   awareness; backend retries on 429 with backoff.
3. PlanExecutor records per-request quota deltas in audit chain.

Effort: M.  Risk: low (degrades to current single-backend behaviour
if MaaS is unavailable).

---

## 5. Suggested execution order

1. **Slot 011 (SPIFFE)** — highest leverage; validates proposal 004
   + unlocks slot 012 + slot 015 with consistent identity.
2. **Slot 014 (TrustyAI + Garak)** — operator-asked-for compliance
   evidence on the RHOAI side; small surface, big credibility win.
3. **Slot 012 (`AgentCard` emission)** — the formal Kagenti adoption
   step.  Lands once SPIFFE is in place to share trust domain.
4. **Slot 015 (LSD consumer)** — symmetrical to our existing vLLM
   `Deploy` flow; cheap to add once Llama Stack reconciler exists.
5. **Slot 013 (Kafka signaling)** — bigger surface, but useful for
   any datacenter customer.  After slot 011 so SPIFFE-backed mTLS
   covers it.
6. **Slot 016 (llm-d + MaaS)** — last, because RHOAI's MaaS surface
   is still moving in 3.4 EA2; let it stabilise.

---

## 6. What this report deliberately leaves alone

- **Notebook-mode integration.**  RHOAI's Notebook surface
  (Workbenches, JupyterHub) is operator-led and doesn't host
  agents.  ACC stays Operator-mode-only.
- **CodeFlare / Distributed Workloads.**  3.4 keeps these for
  training, not for agent fleets.  No alignment work needed.
- **ModelMesh.**  Being de-emphasised in 3.4.  KServe + Llama Stack
  is the supported path; we already align there.
- **Multi-cloud (AKS / CoreWeave / IBM Cloud).**  RHOAI 3.4
  control plane is multi-cloud now, but data-plane agent hosting
  is still OpenShift-only.  Our `deploy_mode` enum
  (`standalone | edge | rhoai`) covers what matters for v0.4.
- **Kagenti's `Component` CRD adoption.**  Tempting (single-agent
  workload abstraction) but would compete with our
  `AgentCollective` reconciler.  Better to interoperate via
  `AgentCard` (slot 012) than re-host our agents on Kagenti's
  Deployment generator.

---

## 7. Open questions for the operator

| # | Question |
|---|---|
| Q1 | Pin to RHOAI 3.4 GA (late May) or 3.3 (current GA) for the slot-011 development branch? |
| Q2 | Does the operator want Kagenti `AgentCard` adoption gated behind a config flag (`kagenti.enabled`) or auto-detect via CRD presence? |
| Q3 | TrustyAI integration: HTTP client to TrustyAI's detector endpoint, or in-process via the TrustyAI Python SDK?  Latter pins us to a TrustyAI version. |
| Q4 | Should the Kafka signaling backend land *in addition to* NATS (two backends, configurable) or *replace* NATS in RHOAI mode? |
| Q5 | MaaS quota awareness: is per-request quota tracking a Cat-A (governance) concern or a Cat-B (operational) concern?  Affects which arbiter sees it. |

---

## Sources

- [Kagenti GitHub org](https://github.com/kagenti)
- [Kagenti operator](https://github.com/kagenti-operator)
- [How Kagenti ADK simplifies production AI agent management — Red Hat Developers](https://developers.redhat.com/articles/2026/05/04/how-kagenti-adk-simplifies-production-ai-agent-management)
- [Zero-trust AI agents on Kubernetes (Kagenti) — next.redhat.com](https://next.redhat.com/2026/03/05/zero-trust-ai-agents-on-kubernetes-what-i-learned-deploying-multi-agent-systems-on-kagenti/)
- [RHOAI 3.4 Release Notes (EA2)](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.4/html/release_notes/index)
- [Red Hat press release — Summit 2026 Agentic Future](https://www.redhat.com/en/about/press-releases/red-hat-unites-builders-and-operators-agentic-future-major-advancements-red-hat-ai)
- [The New Stack — Red Hat AI MaaS / AgentOps](https://thenewstack.io/red-hat-ai-maas/)
- [RHOAI 3.3 — Working with Llama Stack](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.3/html-single/working_with_llama_stack/index)
- [vLLM blog — Llama Stack + vLLM provider](https://blog.vllm.ai/2025/01/27/intro-to-llama-stack-with-vllm.html)

*Report ends.  Next concrete action: open six proposal files in
the Obsidian vault for slots 011–016, then size slot 011 first.*
