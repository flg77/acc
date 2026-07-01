# ACC on RHOAI — end-to-end demo guide

**The canonical, runnable walkthrough**: stand up the operator → deploy a demo
agentset → drive it with golden prompts → watch the **eval-history** light up
with **MLflow** traces on the datacenter. Consolidates the runbooks + use-case
specs into one path; links out for depth rather than duplicating.

> **Audience:** an operator deploying an ACC demo on a RHOAI sandbox.
> **Status:** the demo collectives, operator CRDs, lab-gitops manifests, and
> MLflow integration are all shipped + live-tested; the one moving prerequisite
> is **image currency** (Step 0).

---

## The model (1 minute)

ACC runs an **agentset** (a `collective.yaml`: roles + per-role model + pinned
packages) on a **sovereign runtime** selected by `deployMode` — here **rhoai**.
A lean **assistant** routes operator intent to specialist roles; a **reviewer**
critiques; every action is **governance-gated**. On RHOAI you additionally get
the **datacenter eval loop**: each golden-prompt run is an OTel trace + (proposal G)
a deep-linkable **MLflow** trace, so you can *prove* a role/model change improved
results.

Three shipped demos (`collectives/`):
- **`demo-coding`** — coding_agent + devops + ml_engineer (+ reviewer critic).
- **`demo-financial`** — financial/fpa/contract/risk analysts (read-only; safety-evalled).
- **`demo-multi`** — a hub assistant routing to both as sub-collectives.

---

## Step 0 — Prerequisite: image currency (DO THIS FIRST)

⚠️ **The operator's default agent image is stale.** `AgentCorpus.spec.version`
defaults to **`0.1.0`** (`operator/api/v1alpha1/agentcorpus_webhook.go`), which is
~17 runtime versions behind current spearhead (**v0.5.28**). Deploying as-is runs
agents **without** eval-history, role→model mapping, integrations, or overlay —
the demo would showcase ancient behaviour.

**To run the demo on current code, first publish + pin the v0.5.28 agent + webgui images.**
This is **operator/human-gated** (the Quay push is classifier-blocked for agents):

```bash
# 1. Build BOTH images at the release tag (any Linux podman host: lighthouse / acc1 / bb3 —
#    both Containerfiles are self-contained multi-stage, so only podman is needed).
cd <agentic-cell-corpus @ v0.5.28>
podman build -f container/production/Containerfile.agent-core \
  --build-arg ACC_VERSION=0.5.28 \
  -t quay.io/flg77/acc_images:acc-agent-core-0.5.28 .
podman build -f container/production/Containerfile.webgui \
  --build-arg ACC_VERSION=0.5.28 \
  -t quay.io/flg77/acc_images:acc-webgui-0.5.28 .

# 2. Push to the private registry  ← HUMAN ONLY (operator's `!` pane, podman-login'd as
#    the robot account). BOTH are needed: the operator's WebGUIReconciler pulls acc-webgui-<ver>.
podman push quay.io/flg77/acc_images:acc-agent-core-0.5.28
podman push quay.io/flg77/acc_images:acc-webgui-0.5.28

# 3. Pin the demo AgentCorpus at that version (per-CR override — no operator rebuild needed):
#      spec:
#        version: "0.5.28"
#        imageRepository: "quay.io/flg77/acc_images"
```

**SCC co-requisite — already satisfied by the shipped operator.** The agent-pod SCC
bug (a hardcoded `runAsUser:1001` that OpenShift restricted-v2 rejects at admission —
ReplicaSets `FailedCreate`, zero agents start) was fixed in acc-spearhead **PR #79**
(`8c44f3f`): it drops the pinned UID so the SCC injects the namespace range, keeping
`runAsNonRoot` + a `RuntimeDefault` seccomp profile. **That fix is in operator 0.2.12**
(commit `3a5425b`, verified by ancestry + the live `AgentPodSecurityContext` /
`AgentContainerSecurityContext` helpers), so a current deployment needs **no operator
rebuild** — the per-CR `spec.version` bump above is sufficient. Only an operator
**older than 0.2.0** still needs the bundle bump + catalog redeploy (see
`operator/docs/WS-A-olm-bundle-runbook.md`); bumping the operator's *default*
`spec.version` remains a durable convenience but is optional.

> Until Step 0 lands, you can still rehearse the full flow **on the edge**
> (lighthouse, `deployMode=edge`) where the local image is built from the current
> tree — the eval-history works there; only the MLflow deep-links are DC-only.

---

## Step 1 — Operator + model serving

Mirrors `operator/docs/rhoai-e2e-agentset-runbook.md` (live-validated) and the
lab-gitops wave structure (`lab-gitops/ansible/rhoai-sandbox/manifests/`).

```bash
# Operator (private catalog → OperatorHub install). See WS-A runbook.
oc apply -f operator/config/private-catalog/      # CatalogSource → Subscription

# Model serving (wave 3) — a shared KServe vLLM in acc-system.
oc apply -f .../wave3-model/            # inferenceservice + servingruntime + netpol
```

The demos target a shared in-cluster model (`llama-31-8b-instruct` in the lab
sandbox) cross-namespace; external **MaaS** endpoints (qwen3-14b / llama-scout-17b)
are the alternative per `models.yaml` `maas-*`.

---

## Step 2 — Deploy a demo agentset + MLflow

```bash
# ACC corpus + infrastructure (wave 4) — NATS, Redis, vector store, OTel.
oc apply -f .../wave4-acc/agentcorpus-rhoai.yaml     # set spec.version: "0.5.28" (Step 0)

# MLflow tracking server (wave 5 base) — LIVE (lab-gitops backlog 009).
oc apply -k .../wave5-demo/base/                     # mlflow + dspa + namespace

# The demo (coding shown; finance is symmetric).
oc apply -k .../wave5-demo/demos/coding/             # AgentCorpus + AgentCollective + AccPackageInstall(@acc/workspace-roles)
```

**Wire MLflow into the corpus** so the datacenter eval loop lights up (proposal G P3 +
`docs/observability/eval-history-mlflow.md`):

```yaml
spec:
  deployMode: rhoai
  observability:
    backend: otel
    otelCollector:
      endpoint: http://otel-collector.<ns>.svc:4317
      mlflowEndpoint: http://mlflow.acc-demo.svc:8080      # G: trace fan-out
```
and inject `ACC_MLFLOW_TRACKING_URI=http://mlflow.acc-demo.svc:8080` into the agents
(alongside the model keys). Without these the eval-history still works; only the
"trace →" deep-links stay dark.

---

## Step 3 — Drive the demo (golden prompts)

The demos ship runnable golden prompts (`examples/golden_prompts/`):
- **coding:** `e2e_demo_stock_quotes.yaml` (FastAPI + tests + k8s deploy),
  `demo_coding_unit_test.yaml`.
- **finance:** `demo_financial_contract_risk.yaml`, `demo_financial_runway_forecast.yaml`
  (read-only; the safety beat expects a bounded/refusal verdict).

Drive them from the **TUI Diagnostics pane** (run-selected / run-all) or the CLI
(`acc-cli e2e run <name>`), targeting a role that replies inline (analyst/coding —
the assistant orchestrates rather than replying). Each run lands in the
**run-history timeline** with **tokens · compliance · verdict** (proposal G P2).

Full worked narrative (MaaS, role infusion, reviewed PLAN, Keycloak deploy):
`docs/howto-demo-coding-finance-e2e.md`. Presenter script: the Obsidian
`ACC-UseCaseDemos/acc-demo-storybook-*.md`.

---

## Step 4 — Eval-history → MLflow (the datacenter payoff)

Per run, the **Diagnostics** surface now shows (proposal G):
- **run history** — outcomes of repeated runs (pass/fail · latency · verdict);
- **per-run enrichment** — `tokens in N · cache M · compliance X.XX · verdict`;
- **definition of good** — the deterministic `expects` + the model's `[EVAL_OUTCOME]`;
- **trace →** — a deep link to this run's **MLflow trace** (DC only; the agent
  stamps `acc.task_id` on the span, the collector fans it to MLflow `/v1/traces`).

The per-task compliance record lands in Redis (`acc:{cid}:compliance:task:{task_id}`)
and the OTel span carries compliance + token attrs — so the datacenter sees the
exact run the operator is reviewing. **Promote** a passing golden prompt into a
role's signed eval pack with **→ Eval** (`promoted-evals/<role>/evals/behavior/`),
the regression on-ramp.

> **WebGUI note:** the eval-history surface is **TUI-complete**; the WebGUI
> (RHOAI dashboard) currently shows the golden-prompt *list* only — closing that
> parity gap is the next engineering item (`docs/tui-webgui-parity-gaps.md`).

---

## Step 5 — Verify

| Check | How |
|---|---|
| Agents are current | `oc get acccorpus -o yaml` → `spec.version: 0.5.28`; agent pod image tag = `acc-agent-core-0.5.28` |
| Pods healthy | `oc get pods -n <ns>` all Running (SCC fix shipped in operator ≥0.2.0 / #79) |
| A run completes | Diagnostics run-all → PASS rows with tokens/compliance |
| MLflow trace resolves | run-detail "trace →" link opens the task's trace in MLflow |
| Suite run in MLflow | the `acc-golden-prompts` experiment gains a run |

---

## Troubleshooting

- **Agents on old behaviour** → image is stale; redo Step 0 (`spec.version`).
- **Pods never created / ReplicaSet `FailedCreate` citing `runAsUser`** → operator older than 0.2.0 (pre-#79); upgrade the operator bundle. Operator ≥0.2.0 (incl. the deployed 0.2.12) already drops the pinned UID.
- **No "trace →" link** → `ACC_MLFLOW_TRACKING_URI` unset (edge/unconfigured) — correct on edge; on DC set it + `mlflowEndpoint`.
- **MLflow 403 on writes** → empty CORS allowlist (`MLFLOW_SERVER_CORS_ALLOWED_ORIGINS`) — see lab-gitops backlog 001/009.
- **Model unreachable cross-ns** → the wave3 NetworkPolicy must allow the demo ns → `acc-system`.
- Deeper ops: `operator/docs/howto-operator-ops.md`, `docs/howto-rhoai.md`.

---

## References
- Runbook (live-validated): `operator/docs/rhoai-e2e-agentset-runbook.md`
- Worked example: `docs/howto-demo-coding-finance-e2e.md` · index: `docs/DEMOS.md`
- Eval-history/MLflow wiring: `docs/observability/eval-history-mlflow.md`
- TUI↔WebGUI gaps: `docs/tui-webgui-parity-gaps.md`
- Use-case specs + presenter script: Obsidian `ACC-UseCaseDemos/`
- lab-gitops deploy: `lab-gitops/ansible/rhoai-sandbox/manifests/wave{3,4,5}-*`
- Proposal G (eval-history): Obsidian `ACC-PR/Proposals/PR-PROPOSAL-G`
