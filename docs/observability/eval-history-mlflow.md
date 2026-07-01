# Eval-history → MLflow on RHOAI (proposal G P3)

The Diagnostics → Golden-Prompt **eval-history** surface (PR-PROPOSAL-G) is
**local-first**: run history, version control, and the per-run token /
compliance / verdict enrichment all work on the edge with no external
dependency. On the **RHOAI datacenter** it *lights up further* — each run
becomes an MLflow-rendered **trace** the pane deep-links to, and golden prompts
can be **promoted into a role's eval pack** for regression testing.

This doc is the runtime-side wiring. **Standing up the MLflow tracking server
itself is a gitops task — see `lab-gitops` backlog `009`.** Until it exists the
runtime degrades cleanly (no deep-links; everything else works).

## What the runtime already emits

- **Per-task OTel trace** — `acc.task.process` root span carries `acc.task_id`,
  plus (P2) `compliance_health_score`, `input_tokens`, `cache_read_tokens`
  (`acc/backends/pipeline_tracing.py` + `genai_semconv.py`).
- **Per-task compliance record** — best-effort Redis key
  `acc:{cid}:compliance:task:{task_id}` (`cognitive_core._write_task_compliance_record`,
  7-day TTL) — the DC-side `(task_id → verdict/cost)` join.
- **Suite runs** — `acc/backends/mlflow_runs.py` (`log_golden_results`, opt-in)
  logs a golden-suite execution as one MLflow run when configured. Emitted from
  the **TUI Diagnostics "Run all"** (`_run_prompts`) and the **`acc-cli e2e run`**
  CLI (both via `golden_prompts.persist_results`, `run_meta` seeded by
  `mlflow_runs.base_run_meta` → git_sha + host). No `--history` file required.

## Wiring (operator)

### 1. Inject the tracking URI into the agents

The MLflow run-logging + the eval-history **deep-links** gate on one env var:

```
ACC_MLFLOW_TRACKING_URI = http://mlflow.<ns>.svc:5000     # in-cluster service
ACC_MLFLOW_EXPERIMENT   = acc-golden-prompts              # optional (default)
```

- **Deep-links** (`mlflow_trace_url` / `mlflow_experiment_url`) need **only the
  URI** — not the `mlflow` python package — because they're browser URLs the
  operator opens. So the Diagnostics pane shows "trace →" links as soon as the
  URI is set, even on a TUI without the `acc[mlflow]` extra.
- **Run logging** (`log_golden_results`) additionally needs the `mlflow`
  client. The **acc-tui image bundles `mlflow-skinny`** (`Containerfile.tui`),
  so Diagnostics "Run all" logs runs out of the box once the URI is set; other
  runners need the `acc[mlflow]` extra.

### 1b. Local / compose activation (no cluster)

The acc-tui compose service reads `ACC_MLFLOW_TRACKING_URI` from `./.env` — set
it and restart, no compose edit:

```
# ./.env
ACC_MLFLOW_TRACKING_URI=https://mlflow.example/     # cluster route, or a
                                                    # standalone `podman run mlflow`
```

```
./acc-deploy.sh restart          # rolls the TUI onto the env (down + up --force-recreate)
```

Empty / unset = no-op (edge default). Then Diagnostics "Run all" populates the
`acc-golden-prompts` experiment and the pane's `trace →` links resolve.

### 2. Fan agent traces to MLflow (DC only)

The operator's OTel collector already supports an MLflow `/v1/traces` fan-out —
set it on the AgentCorpus:

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCorpus
metadata:
  name: my-corpus
spec:
  deployMode: rhoai
  observability:
    backend: otel
    otelCollector:
      endpoint: http://otel-collector.<ns>.svc:4317        # primary (Tempo etc.)
      mlflowEndpoint: http://mlflow.<ns>.svc:5000           # → MLflow /v1/traces (G)
```

(`operator/internal/templates/otel_config.go` renders the `otlphttp/mlflow`
exporter; `operator/api/v1alpha1/agentcorpus_types.go` is the API.) With this,
a golden run's `task_id` resolves to its trace in MLflow's Traces UI, and the
pane's "trace →" link lands the operator on it.

### Edge contrast (no change needed)

On `deployMode: edge` the collector is **not deployed**
(`observability/otel_collector.go`), `observability.backend` defaults to `log`,
and `ACC_MLFLOW_TRACKING_URI` is unset — so:

- the eval-history surface still works (local `run_history.jsonl`, versions,
  token/compliance/verdict from the reply path);
- the per-task compliance record still writes to the edge's local Redis;
- the pane shows **no** "trace →" link (correct — there's no MLflow to link to);
- comparability edge↔DC is via the **portable eval JSONL** (run the same suite
  on both tiers and compare), not a telemetry bridge.

## Golden → eval-pack promotion (role testing)

In Diagnostics, **→ Eval** adapts the editor's golden prompt to a `BehaviorEval`
(`acc.pkg.evals.from_golden_prompt`) and writes it, pkg-shaped, to the writable
store:

```
<ACC_GOLDEN_WRITABLE_ROOT>/promoted-evals/<role>/evals/behavior/<name>.yaml
```

`acc.pkg.evals.load_evals(<…>/promoted-evals/<role>)` reads it back, so the
promoted dir is a ready eval-pack root to fold into a signed `@acc/<role>-roles`
pack (the signing/publish pipeline is `lab-gitops` 006 / the ecosystem flow).
This is the on-ramp from an ad-hoc prompt to a versioned, signed regression eval
runnable against the curated-LLM panel.

## Verify

- **Local (edge / any):** drive a golden run → the run-detail shows tokens ·
  compliance · verdict; **→ Eval** writes a loadable `BehaviorEval`.
- **DC:** with the two settings above + the MLflow server (lab-gitops 009),
  the run-detail shows a "trace →" link that resolves to the task's MLflow trace;
  `log_golden_results` lands a run in the `acc-golden-prompts` experiment.

## References

- `ACC-PR/Proposals/PR-PROPOSAL-G` — the eval-history surface (P1 history+VC,
  P2 task_id enrichment, P3 this).
- `lab-gitops` backlog `009` — provision the MLflow tracking server in the DC.
- `acc/backends/mlflow_runs.py`, `acc/pkg/evals.py`,
  `acc/tui/screens/diagnostics.py`, `acc/backends/pipeline_tracing.py`.
