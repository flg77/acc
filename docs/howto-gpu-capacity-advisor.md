# How-to: the GPU capacity advisor's building blocks

The GPU capacity advisor (proposal 057) is an ACC agent that watches how GPUs
are used by served models on OpenShift AI and recommends — and, with a human's
approval, carries out — capacity changes. Backlog 019 lists what ACC lacked
for that. This page covers what is now in place:

| Gap | What landed | Where |
|---|---|---|
| A1 | `openai_compat` as an AgentCollective LLM backend (RHOAI MaaS) | operator `spec.llm.openaiCompat` |
| A2 | A model registry delivered to agents on the cluster | operator `spec.modelRegistry`, `agents[].model` |
| A6 | RHOAI MaaS entries in the example registry | `models.yaml.example` |
| A3 | Read-only PromQL | skill `prometheus_query` |
| A4 | Feast online features (read-only) | MCP `feast` |
| A5 | Governed stop/start of a served model | skill `ocp_model_control` |
| — | vLLM memory sizing ported from ConfigIQ | skill `gpu_capacity_estimate`, `acc/gpu_sizing.py` |
| — | ConfigIQ's latency-aware sizing API | MCP `aisimulators` |

The three skills and the two MCP manifests ship together as the
`capacity_advisor` feature (`features/capacity_advisor.yaml`). The advisor
itself is the `capacity_advisor` role, covered in section 8.

## 1. Talk to RHOAI MaaS from a collective

Sections 1 and 2 need an operator release with the `openaiCompat`,
`modelRegistry` and `agents[].model` fields
([flg77/acc-spearhead-operator#33](https://github.com/flg77/acc-spearhead-operator/pull/33),
after 0.3.0). Operator 0.3.0 does not know them.

```yaml
apiVersion: acc.ic3networks.com/v1alpha1
kind: AgentCollective
metadata:
  name: advisor
  namespace: acc-advisor
spec:
  collectiveId: advisor
  corpusRef: { name: acc }
  llm:
    backend: openai_compat
    openaiCompat:
      baseUrl: https://maas.apps.<cluster-domain>/<namespace>/<model>/v1
      model: <model>
      apiKeySecretRef: { name: maas-key, key: api_key }
  agents:
    - role: assistant
```

The key reaches the agent as `ACC_OPENAI_COMPAT_API_KEY`, and the rendered
`acc-config.yaml` names that variable as `llm.api_key_env`. Omit
`apiKeySecretRef` for an endpoint that takes no key.

MaaS API keys need RHOAI ≥ 3.5.1 when the user belongs to a group whose name
contains a space; older releases mint a key that returns 401.

## 2. Pick a model per agent from a registry

Put a `models.yaml` in a ConfigMap and point the collective at it:

```yaml
spec:
  modelRegistry:
    configMapRef: { name: acc-models }   # key defaults to models.yaml
  agents:
    - role: assistant
      model: maas-diffusiongemma          # a model_id in the registry
      extraEnv:
        - name: MAAS_RHOAI_API_KEY        # the entry's api_key_env
          valueFrom: { secretKeyRef: { name: maas-key, key: api_key } }
```

The operator copies the registry into the collective's `acc-config` ConfigMap
as `/etc/acc/models.yaml` and sets `ACC_MODELS_PATH`. `agents[].model` sets
`ACC_AGENT_MODEL_ID`, which takes precedence over the registry's `role_models`.
The registry's digest is set on the pod template, so editing the ConfigMap
rolls the agents on the next reconcile. If the ConfigMap or its key is missing,
the reconcile fails; it does not fall back to `spec.llm`. The webhook refuses
`agents[].model` when no registry is declared.

## 3. Configure the skills

Set these on the advisor agent with `agents[].extraEnv`:

| Variable | Skill | Default |
|---|---|---|
| `ACC_PROMETHEUS_URL` | `prometheus_query` | none (skill refuses to run) |
| `ACC_PROMETHEUS_TOKEN` | `prometheus_query` | the pod's service-account token |
| `ACC_PROMETHEUS_CA_FILE` | `prometheus_query` | the in-cluster service CA |
| `ACC_PROMETHEUS_QUERY_ALLOWLIST` | `prometheus_query` | none (every query allowed) |
| `ACC_OCP_CONTROL_NAMESPACES` | `ocp_model_control` | none (every call refused) |
| `ACC_KUBERNETES_API_URL` / `_TOKEN` / `_CA_FILE` | `ocp_model_control` | in-cluster |

Any token variable can come from the corpus credentials Secret
(`spec.secretMount`) instead of the environment.

### RBAC

**Metrics (read).** On OpenShift, bind `cluster-monitoring-view` to the agent's
service account, and point `ACC_PROMETHEUS_URL` at
`https://thanos-querier.openshift-monitoring.svc:9091`. A narrower option is
the tenancy port 9092 with a namespace-scoped grant. That port requires a
`namespace` label on every query.

**Model control (act).** Use a Role, not a ClusterRole, in each namespace
listed in `ACC_OCP_CONTROL_NAMESPACES`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: acc-model-control, namespace: flg1 }
rules:
  - apiGroups: [serving.kserve.io]
    resources: [llminferenceservices, inferenceservices]
    verbs: [get, patch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: acc-model-control, namespace: flg1 }
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: Role, name: acc-model-control }
subjects:
  - { kind: ServiceAccount, name: <agent service account>, namespace: acc-advisor }
```

## 4. How `ocp_model_control` is governed

* **Manifest.** `risk_level: HIGH`, `system_access: true`,
  `requires_oversight: true` and `requires_actions: [control_model_serving]`. A role has to grant the skill,
  the action and a HIGH skill-risk ceiling. If any of these is missing, the
  call becomes an escalation question instead of running.
* **Gate.** Because the manifest declares `requires_oversight`, every call goes
  to the human oversight queue in `AUTO`, `ACCEPT_EDITS` and
  `ASK_PERMISSIONS`. `PLAN` dispatches nothing.
* **No queue, no call.** In a deployment with no oversight queue, the
  dispatcher refuses the call before it reaches the skill: "needs a human
  approval for every call and there is no oversight queue to ask". It never
  runs ungated.
* **Preview.** Before the operator decides, the decision panel runs a
  server-side dry run (`dryRun=All`). It shows that the object exists and that
  RBAC allows the patch, and it changes nothing.
* **Allowlist.** The skill checks the namespace allowlist before any request
  leaves the pod. The allowlist comes from the deployment, not from the model's
  arguments.
* **Action.** The skill merge-patches `serving.kserve.io/stop` (`"true"` to
  stop, `"false"` to start). It also records the stated reason in
  `acc.ic3networks.com/model-control-reason` on the object.

## 5. Size a model

`gpu_capacity_estimate` takes a Hugging Face `config.json`, which the agent
fetches with `http_get` from `https://huggingface.co/<id>/raw/main/config.json`,
and a GPU id:

```text
[SKILL: gpu_capacity_estimate {"model_config": {...}, "gpu": "h100_sxm",
  "max_model_len": 32768, "avg_tokens_per_request": 3000, "target_concurrency": 64}]
```

It returns a per-GPU memory breakdown: weights, activations, NCCL, the 3.5 GiB
runtime reserve and the KV cache. It also returns the KV-cache size in tokens,
concurrent sequences at `max_model_len` and at the average request size, the
smallest tensor-parallel size that fits, a `serving_config` for vLLM, and
`replicas_needed`/`gpus_needed` for a target concurrency.

The math is ported from **ConfigIQ**
([configiq.xyz](https://configiq.xyz), `redhat-performance/configiq`). It lives
in that project's SDK, `redhat-performance/aisimulate` (Apache-2.0, derived
from NVIDIA AIConfigurator). The port covers:

* per-token KV bytes for GQA/MHA and MLA, and the sliding-window hybrid inverse;
* the weight estimate. ACC counts K/V projections at their GQA width, shards MoE
  experts by tensor parallelism (vLLM's default), and reads
  `quantization_config`;
* vLLM's budget rule (`gpu_memory_utilization × total − non-KV`, with a 2 %
  tolerance), the activation coefficients, the 70 MiB activation floor, the
  NCCL table and the runtime reserve;
* the GPU table, from aisimulate's `systems/*.yaml`.

The port does not predict latency. TTFT/TPOT and SLA-driven TP/replica
search need ConfigIQ's measured kernel databases. Those are served by its
`aisimulators` API, which ACC reaches through the `aisimulators` MCP manifest
described in the next section.

Until that server is deployed, treat `gpu_capacity_estimate` as a capacity
floor. Check it against live `vllm:kv_cache_usage_perc` and
`vllm:num_requests_running` with `prometheus_query`.

## 6. Latency-aware sizing: the `aisimulators` MCP server

`mcps/aisimulators/mcp.yaml` exposes ConfigIQ's AISimulators service as six
LOW-risk tools. They compute only: they read nothing from the cluster and
change nothing.

| Tool | Endpoint | Use |
|---|---|---|
| `post_recommend_recommend_post` | `POST /recommend` | best GPU/TP/PP/replica configs under TTFT/TPOT SLAs |
| `post_predict_predict_post` | `POST /predict` | TTFT, TPOT and throughput for one configuration |
| `post_memory_memory_post` | `POST /memory` | per-GPU memory and KV-cache capacity |
| `get_systems_systems_get`, `get_models_models_get`, `get_backends_backends_get` | `GET` | what it can size |

The tool names are the FastAPI operation ids that fastapi-mcp publishes.
`/metrics` and the deprecated `/estimate` are denied.

**Deploy.** Build `services/aisimulators/Containerfile` from
`redhat-performance/configiq` with the `mcp` extra. Run it as Service
`acc-mcp-aisimulators` on port 7860. The manifest points at
`http://acc-mcp-aisimulators:7860/mcp/http` over MCP streamable HTTP. The
SSE endpoint at `/mcp` is unchanged and is not what ACC uses.

**Upstream prerequisite.** As released (configiq `1cd8145`), the service
cannot be used from ACC, for two reasons:

1. It mounts MCP with fastapi-mcp `mount_sse("/mcp")`. ACC speaks streamable
   HTTP, not SSE, so its client fails at `initialize` (measured: "invalid
   JSON-RPC envelope from initialize").
2. It mounts before `/recommend`, `/predict`, `/memory`, `/models` and
   `/systems` are declared. fastapi-mcp 0.4 snapshots the routes when it is
   constructed, so only `get_backends` is exposed (measured).

The fix is drafted on configiq branch
`fix/mcp-expose-all-routes-and-streamable-http` and has not been submitted
upstream yet. It makes three changes:

* `configiq.mcp.mount()` keeps SSE at `/mcp` and adds streamable HTTP at
  `/mcp/http` on the same server.
* `aisimulators` and `aicostings` mount after their last route. `aicostings`
  had the same bug and exposed no tools at all.
* Tests assert the full tool list over both transports.

ACC's client was run against the real patched `aisimulators` service at
`/mcp/http`. It saw exactly the six allowed tools, `get_systems` succeeded,
and `/metrics` stayed denied.

**Egress.** For a `model_path` that is not one of its built-in models, the
service fetches `config.json` from huggingface.co. Allow that egress, or pass
`model_config` yourself.

## 7. Read features: the `feast` MCP server

Feast's feature server has MCP built in (`feast[mcp]`). `mcps/feast/mcp.yaml`
connects to it read-only. It allows `get_online_features` and `health`. It
denies `push`, `write_to_online_store`, `materialize`,
`materialize_incremental` and the demo chat endpoints, which the same server
also exposes. A tool that a later Feast release adds is not allowed until
someone lists it.

**Enable it** in the feature store's `feature_store.yaml`:

```yaml
feature_server:
  type: mcp
  enabled: true
  mcp_enabled: true
  mcp_transport: http   # streamable HTTP at /mcp; the default `sse` is not something ACC speaks
```

**Point ACC at it.** Expose the online feature server as Service
`acc-mcp-feast` on port 6566, or overlay the manifest's `url`. If the server
enforces auth, set `api_key_env` to the variable that holds the bearer token.

**Read.** Call `get_online_features_get_online_features_post` with
`entities` (`{join_key: [values]}`) and either `feature_service` or
`features` (`"view:feature"`). The result is Feast's column-oriented JSON:
`results[j]` is feature `metadata.feature_names[j]`, with the join key first.
`values[i]` and `statuses[i]` belong to entity row `i`. A status other than
`PRESENT` (for example `NOT_FOUND`) means the store has no value for that row.

This was checked against a real `feast serve` (feast 0.66.0) with a
materialized SQLite online store, through ACC's client. Only the two reads
were offered. `get_online_features` returned the stored values and reported
`NOT_FOUND` for an unknown entity. All four write tools were refused.

## 8. The advisor: `capacity_advisor` role and collective

`roles/capacity_advisor/role.yaml` is the agent from proposal 057, and
`collectives/collective.capacity-advisor.yaml` is the collective that runs it.

**Collective.**

| Agent | Model | Job |
|---|---|---|
| `capacity_advisor` | `maas-diffusiongemma` | decides |
| `reviewer` | `maas-granite-8b` (Gaudi2) | explains |

**Every 15 minutes** the advisor wakes on its own timer (`proactive_wakeup`,
`wakeup_interval_s: 900`).

**Before it reasons, it reads.** `wakeup_prefetch` lists four Feast reads.
They run before the wake-up's model turn:

1. `gpu_queue` for the cluster. This includes `deployment_ids`, the list of
   every GPU-holding deployment.
2. `gpu_node_stats` for the GPU nodes.
3. `model_usage` for the deployments listed in read 1.
4. `model_predictions` for the same deployments. This is a separate read so
   that a store without the P2 view loses only this read.

Reads 3 and 4 name their entities as `"@prefetch:deployment_ids"`. Before
either read runs, that value is replaced with the list read 1 returned. If no
earlier read returned it, the read is skipped with an error rather than sent
with the placeholder. The feature definitions, including `deployment_ids`,
are in `examples/capacity_advisor/feature_repo/`.

The results reach the model as compact evidence, one line per entity
(`model_deployment=flg1/x | gpus_held=1 | ...`). A value with no data shows as
`MISSING`. If anything does not fit the 12,000-character budget, the number
of omitted lines is stated.

This is needed because ACC dispatches only the first turn's markers. Without
the prefetch, a read followed by a decision would lose the decision: the
proposal would arrive in a follow-up turn, and those are never dispatched.

**What the prefetch may run.** Prefetch entries run with no human in the
loop, so they are restricted to plain reads. Each entry must:

* be LOW risk;
* have no gate category;
* not be destructive or `requires_oversight`;
* be an allowed tool of its MCP server.

Anything else is refused and shown to the model as an error. Both MCP
manifests declare `system_access: false` and `acts_on_behalf: false`.
Without that, fastapi-mcp's `..._post` tool names would read as "acts on the
operator's behalf".

**It decides and proposes.** The policy is in the role's `seed_context`.
Allowlisted projects are `flg1` plus the capacity-admins projects; everything
else is report-only. A deployment counts as idle at 60 minutes or more.

The advisor proposes at most one action per wake-up, as an `ocp_model_control`
marker. That call waits in the oversight queue for a capacity-admins
approver, who sees the reason and a dry run. The role runs in
`ASK_PERMISSIONS` mode, so anything else the model asks for is also asked
first.

**It can query Prometheus on request.** The role is granted
`prometheus_query` (read-only, LOW). In an operator's review or sizing task
it can check a number Feast does not carry, or a `gpu_capacity_estimate`
against live `vllm:kv_cache_usage_perc` and `vllm:num_requests_running`.
The result is attached to the task's `TASK_COMPLETE` for the operator. The
model does not see it, because the role does not set `tool_result_turn`.
The prompt therefore tells the advisor never to base a proposal on a query
from the same reply. The wake-up reads only the Feast prefetch. The skill
needs `ACC_PROMETHEUS_URL` and the metrics RBAC above.

**Before deploying:**

* **Fill `gpu_queue.deployment_ids` in the feature job.** It is the
  advisor's list of deployments; a deployment missing from it is not seen.
* **Set the allowlist on the deployment.** `ACC_OCP_CONTROL_NAMESPACES` and the
  namespaced Role (section 3) enforce it; the prompt only states it.
* **Gaps compared with 057:**
  * The reviewer reviews PLANs routed to it. It does not sit between a
    wake-up proposal and the human; the human approval is the gate.
  * DiffusionGemma's logprob confidence (057 G3) is not read: the
    `openai_compat` backend does not request logprobs.

## 9. Useful queries

```promql
# GPUs requested, per namespace
sum by (namespace) (kube_pod_container_resource_requests{resource="nvidia_com_gpu"})
# GPU utilisation (DCGM exporter)
avg by (namespace, pod) (DCGM_FI_DEV_GPU_UTIL)
# vLLM KV-cache pressure and load, per served model
max by (model_name) (vllm:kv_cache_usage_perc)
sum by (model_name) (vllm:num_requests_running)
sum by (model_name) (vllm:num_requests_waiting)
```
