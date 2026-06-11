# 20260608-edge-telemetry-nats-mlflow — proposal

## Why

Building the detached-edge → RHOAI escalation demos (use-case scenarios in the
vault under `ACC-UseCaseDemos/`) surfaced a real edge-operations question: *"How
do we port the edge's NATS scenario to the datacenter MLFlow instance, while
staying within edge boundaries?"* In a realistic edge test the edge is
**NATS-only egress** and may be **intermittently connected** — it has no direct
line to the DC MLFlow.

Today that gap is total:

* **Edge mode disables the OTel collector.**
  `operator/internal/reconcilers/observability/otel_collector.go` (≈L46-52)
  returns early when `corpus.Spec.DeployMode == DeployModeEdge`, with the inline
  rationale that a collector "requires a persistent network connection to the
  observability stack which may not be available at the edge." Edge CR samples
  set `observability.backend: log` (`docs/howto-edge.md`).
* **No telemetry crosses the NATS leaf→hub link.** Only task delegation
  (`acc.bridge.{cid}.delegate`, ACC-9 federation in `acc/a2a/federation.py`)
  forwards to the hub; intra-collective subjects stay local
  (`docs/howto-edge.md`). Audit is file/Kafka (`acc/audit.py`); traces are direct
  OTLP (`acc/backends/pipeline_tracing.py`, `metrics_otel.py`). There is **no
  NATS↔OTLP/MLFlow bridge**.

So the edge's `acc.task.process` traces never reach the DC MLFlow. The demos
work around this with **portable evals** (the same golden + safety suite run on
both tiers, comparable JSONL keyed by model/host — `acc/golden_prompts.py`,
`acc/pkg/evals.py`), which is the honest near-term answer. But the operator wants
the *option* to centralize edge observability in the DC MLFlow **without** giving
the edge a direct egress path — telemetry should ride the same NATS uplink the
escalation already uses, and survive disconnections.

This proposal adds that bridge as an opt-in capability.

## What changes

### Phase 1 (this ship — vX.Y.Z) — NATS span-summary relay

The crisp, edge-boundary-respecting design: carry **compact span summaries** over
the NATS leaf→hub link; reconstruct them into the DC MLFlow on the datacenter
side; buffer across disconnects with JetStream.

* **Edge publisher** (`acc/backends/telemetry_bridge.py`, NEW; opt-in): after the
  root `acc.task.process` span closes in `acc/cognitive_core.py`, emit a compact
  summary — `{trace_id, span_id, task_id, collective_id, role, model,
  input_tokens, output_tokens, latency_ms, drift_score, eval_outcome,
  stage_timings[]}` (~50-300 bytes) — and publish to a **hub-forwarded** subject
  `acc.{edge_cid}.telemetry.summary`. This subject is *not* intra-collective, so
  it crosses the leaf→hub link by the same rule that forwards `acc.bridge.*`.
  No verbose reasoning text leaves the edge (privacy + bandwidth).
* **DC relay** (`acc/backends/telemetry_relay.py`, NEW; runs in the DC): subscribe
  to `acc.*.telemetry.summary` (durable JetStream consumer), reconstruct a
  synthetic OTel span tree with the GenAI semconv (reuse
  `acc/backends/genai_semconv.py` + the `pipeline_tracing.py` span shape), and
  export via the existing OTLP→MLFlow path (`metrics_otel.py`). The edge's run
  shows up in the DC MLFlow Trace UI as an `acc.task.process` trace tagged
  `acc.tier=edge` + `acc.collective_id=<edge_cid>`.
* **Store-and-forward**: the summary subject is JetStream-backed so summaries
  buffer on the edge leaf when the hub is unreachable and flush on reconnect; the
  DC relay's durable consumer replays them. This is the "intermittent edge"
  property.
* **Config (opt-in, no-op by default):**
  * Edge: `EdgeSpec.telemetryBridge: bool` (operator) → env
    `ACC_EDGE_TELEMETRY_BRIDGE=1`.
  * DC: `ObservabilitySpec.edgeTelemetryEnabled: bool` → the operator renders the
    relay Deployment + the JetStream stream/consumer.

### Phases 2–N (deferred)

* **Phase 2 — full-fidelity collector relay (Arch 3).** Run a lightweight OTel
  Collector at the edge with a custom NATS exporter; a DC Collector with a NATS
  receiver; forward *complete* spans (reasoning, tool calls) over NATS, no
  summarization. Larger (OTel Collector-Contrib plugin work); the parity target.
* **Phase 3 — air-gap file-sync (Arch 1).** For fully offline edges: accumulate
  `FileAuditBackend` JSONL + a local span spool, and a store-and-forward sync
  service uploads to the DC MLFlow on reconnect. The disconnected-first fallback.
* **Phase 4 — MLFlow Runs comparison.** Once the run-logging seam lands (PR #49,
  `acc/backends/mlflow_runs.py`), log edge + DC runs as comparable MLFlow **Runs**
  (a metrics table), not just traces — turning the eval-JSONL diff into a native
  MLFlow comparison.

## Impact

* **Affected code (Phase 1):**
  * `acc/backends/telemetry_bridge.py` — NEW edge publisher (~120 LOC; lazy, opt-in).
  * `acc/backends/telemetry_relay.py` — NEW DC consumer + span reconstruction (~250 LOC).
  * `acc/cognitive_core.py` — emit the summary at root-span close (1 call site,
    guarded by `ACC_EDGE_TELEMETRY_BRIDGE`).
  * `acc/backends/genai_semconv.py` + `pipeline_tracing.py` — reuse the semconv
    mapping + span shape for reconstruction (no behavior change).
  * `operator/api/v1alpha1/agentcorpus_types.go` — `EdgeSpec.TelemetryBridge` +
    `ObservabilitySpec.EdgeTelemetryEnabled`; a relay Deployment reconciler +
    JetStream stream/consumer config.
* **New env knobs:** `ACC_EDGE_TELEMETRY_BRIDGE` (edge, default off);
  relay endpoint inherits the DC `OTEL_EXPORTER_OTLP_*` config.
* **Tests:** publisher emits the correct summary shape on span close; relay
  reconstructs a schema-valid OTel span + exports it; JetStream durable consumer
  replays after a simulated disconnect; opt-out is a strict no-op (no subject, no
  publish). ~15 new across 2-3 files.
* **Backward compatibility:** purely additive + opt-in. Disabled by default →
  no new subjects, no behavior change. The portable-eval comparison
  (`acc/golden_prompts.py`, `acc/pkg/evals.py`) remains the default cross-tier
  story; this bridge augments it, doesn't replace it.

## What stays open after Phase 1

* Full-fidelity span forwarding (Phase 2) — Phase 1 is **summary-only** by design
  (no verbose reasoning leaves the edge).
* Air-gap file-sync (Phase 3) for edges with *no* hub link at all.
* MLFlow **Runs** comparison table (Phase 4, gated on PR #49).
* Multi-edge subject cardinality / tenancy at scale (per-edge-cid vs hub-wide
  fan-in) — Phase 1 scopes to `acc.{edge_cid}.telemetry.summary`.
* **Privacy decision to confirm with the operator:** Phase 1 forwards *summaries
  only* (no reasoning text/PII). Keep that ceiling through later phases unless the
  operator explicitly opts a trusted edge into full-span forwarding.

## References

* Demos that motivated this: `…\AgenticCellCorpus\ACC-UseCaseDemos\acc-demo-codingagent-2026-06-08.md`
  + `acc-demo-financeagentset-2026-06-08.md` (the detached-edge comparability beat).
* Brainstorm (long-form, 3 architectures + tradeoff matrix):
  `…\AgenticCellCorpus\ACC Openspec\Edge-Telemetry-NATS-MLFlow\Edge telemetry over NATS — brainstorm.md`
* Current telemetry: `acc/backends/pipeline_tracing.py`, `metrics_otel.py`,
  `genai_semconv.py`; `docs/observability/mlflow.md`;
  `deploy/observability/otel-collector.yaml`.
* Edge constraints: `operator/internal/reconcilers/observability/otel_collector.go`
  (≈L46-52), `docs/howto-edge.md`, EdgeSpec in
  `operator/api/v1alpha1/agentcorpus_types.go`.
* Federation/leaf→hub: `acc/a2a/federation.py`, `acc/agent.py` (`_delegate_task`).
* Related: `20260527-mlflow-otel-telemetry` (the OTel trace export this builds on);
  PR #49 (the MLFlow Runs-logging seam, Phase 4 dependency).
