# 20260608-edge-telemetry-nats-mlflow — tasks

## Phase 1 (vX.Y.Z) — NATS span-summary relay

### 1.1 Edge publisher
- [ ] `acc/backends/telemetry_bridge.py` — `publish_summary(summary, *, nc, edge_cid)`;
      lazy import, no-op when `ACC_EDGE_TELEMETRY_BRIDGE` unset.
- [ ] Summary schema (compact, ~50-300 B): `{trace_id, span_id, task_id,
      collective_id, role, model, input_tokens, output_tokens, latency_ms,
      drift_score, eval_outcome, stage_timings[]}`. **No reasoning text / PII.**
- [ ] Publish to `acc.{edge_cid}.telemetry.summary` (hub-forwarded subject, NOT
      intra-collective) so it crosses the leaf→hub link.
- [ ] Wire one call site in `acc/cognitive_core.py` at root-span (`acc.task.process`)
      close, guarded by the env flag.

### 1.2 DC relay
- [ ] `acc/backends/telemetry_relay.py` — durable JetStream consumer on
      `acc.*.telemetry.summary`.
- [ ] Reconstruct a synthetic OTel span tree using `acc/backends/genai_semconv.py`
      + the `pipeline_tracing.py` span shape; tag `acc.tier=edge`,
      `acc.collective_id=<edge_cid>`.
- [ ] Export via the existing OTLP→MLFlow path (`metrics_otel.py` exporter setup).

### 1.3 Store-and-forward
- [ ] JetStream stream + durable consumer for the summary subject so summaries
      buffer on the edge leaf when the hub is down and replay on reconnect.

### 1.4 Operator config
- [ ] `EdgeSpec.TelemetryBridge bool` → env `ACC_EDGE_TELEMETRY_BRIDGE=1`.
- [ ] `ObservabilitySpec.EdgeTelemetryEnabled bool` → render the relay Deployment
      + the JetStream stream/consumer (DC side only).
- [ ] Deepcopy + CRD regen (`make manifests`); CSV/bundle unchanged unless RBAC
      for the relay Deployment is needed.

### Tests
- [ ] `tests/test_telemetry_bridge.py` — summary shape on span close; opt-out is a
      strict no-op (no subject created, no publish).
- [ ] `tests/test_telemetry_relay.py` — reconstructs a schema-valid OTel span +
      exports it (mocked exporter); durable consumer replays after a simulated
      disconnect.

### Verification (Phase 1)
- [ ] Targeted: `pytest tests/test_telemetry_*.py`.
- [ ] Full sweep: `pytest tests/ --ignore=tests/container` stays green.
- [ ] Lighthouse smoke: set `ACC_EDGE_TELEMETRY_BRIDGE=1` on lighthouse + the relay
      on the DC hub → run an edge task → confirm an `acc.task.process` trace tagged
      `acc.tier=edge` appears in the DC MLFlow Trace UI.
- [ ] Disconnect smoke: stop the hub link, run 2 edge tasks, restore the link →
      both summaries replay into MLFlow (no loss).

## Phase 2 (deferred) — full-fidelity collector relay (Arch 3)
- [ ] Edge lightweight OTel Collector + custom NATS exporter (Contrib-style plugin).
- [ ] DC Collector NATS receiver → existing MLFlow/Phoenix fan-out.
- [ ] Forward complete spans (reasoning, tool calls), not summaries — privacy gate.

## Phase 3 (deferred) — air-gap file-sync (Arch 1)
- [ ] Edge: accumulate `FileAuditBackend` JSONL + a local span spool.
- [ ] Store-and-forward sync service uploads to the DC MLFlow on reconnect.
- [ ] For edges with no hub link at all.

## Phase 4 (deferred) — MLFlow Runs comparison
- [ ] Gate on PR #49 (`acc/backends/mlflow_runs.py`).
- [ ] Log edge + DC golden/eval runs as comparable MLFlow **Runs** (metrics table),
      turning the eval-JSONL diff into a native MLFlow comparison.
