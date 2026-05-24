# Operator guide — instantiating agentsets via the ACC operator

How to express an **agentset** (the roles, replicas, models, and behaviour of a
collective) as an `AgentCollective` CR for the DC/RHOAI path — the operator
analogue of the standalone `collective.yaml`.

## Standalone → operator field map

| Standalone (`collective.yaml`, `acc/collective.py`) | Operator (`AgentCollective.spec`, `agentcollective_types.go`) |
|---|---|
| `collective_id` | `collectiveId` |
| `agents[].role` | `agents[].role` |
| `agents[].replicas` | `agents[].replicas` |
| `agents[].purpose` | (per-agent purpose) → `agents[].extraEnv` `ACC_AGENT_PURPOSE` |
| `agents[].cluster_id` | `agents[].extraEnv` `ACC_CLUSTER_ID` |
| `agents[].model` (PR-MM1) | **no clean field yet** → `agents[].extraEnv` (see below) |
| `agents[].extra_env` | `agents[].extraEnv` (`[]EnvVar`) |
| collective default LLM | `spec.llm` (`LLMSpec`: ollama/anthropic/vllm/llama_stack) |
| `worker_pool` | (no operator equivalent; KEDA `spec.scaling` covers elasticity) |
| role behaviour (`role.yaml`) | `spec.roleDefinition` (collective-wide) **or** the embedded `roles/` tree |

The **5 canonical roles** the operator validates: `ingester`, `analyst`,
`synthesizer`, `arbiter`, `observer`. Coding subroles etc. come from the
embedded `roles/` tree (delivered as ConfigMaps; see the parity doc).

## Baseline trio

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCollective
metadata: { name: acc-collective-01, namespace: acc-system }
spec:
  collectiveId: acc-01
  corpusRef: { name: acc-corpus }
  agents:
    - { role: ingester, replicas: 1 }
    - { role: analyst,  replicas: 1 }
    - { role: arbiter,  replicas: 1 }
  llm:
    backend: vllm
    vllm: { inferenceServiceRef: acc-01-llm, model: meta-llama/Llama-3.2-3B-Instruct, deploy: false }
```

## Per-agent model — multimodel via `extraEnv` (today)

There is **no `agents[].model` field yet**, but `AgentRoleSpec.extraEnv` is the
escape hatch (it mirrors standalone `AgentSpec.extra_env`, which already
threads per-agent LLM env). Set the same env vars `acc.models.model_env`
emits, so cheap workers + a powerful reviewer coexist:

```yaml
spec:
  collectiveId: acc-rev-01
  corpusRef: { name: acc-corpus }
  llm: { backend: anthropic, anthropic: { model: claude-haiku-4-6, apiKeySecretRef: { name: acc-anthropic, key: ACC_ANTHROPIC_API_KEY } } }  # collective default = cheap
  agents:
    - role: analyst        # worker — inherits the cheap collective default
      replicas: 2
    - role: arbiter        # reviewer — override to a powerful model
      replicas: 1
      extraEnv:
        - { name: ACC_LLM_BACKEND, value: anthropic }
        - { name: ACC_ANTHROPIC_MODEL, value: claude-sonnet-4-6 }
```

> Verify the reconciler injects `AgentRoleSpec.extraEnv` into the pod container
> (intended; confirm on first bring-up). The clean `agents[].model` field +
> resolving it against a registry is the tracked follow-up.

## Memory reflection + prompt cache via `extraEnv`

Same mechanism — these are env-driven (the role-flag `memory_reflection` is
carried by the embedded `role.yaml`; the cadence + cache are env):

```yaml
    - role: analyst
      extraEnv:
        - { name: ACC_REFLECTION_INTERVAL_S, value: "3600" }   # PR-MEM2 loop on
        - { name: ACC_LLM_ENABLE_PROMPT_CACHE, value: "true" } # PR-CA2
```

## Collective-wide role behaviour

`spec.roleDefinition` sets purpose/persona/seedContext/taskTypes/allowedActions/
`categoryBOverrides` for the whole collective (rendered to a ConfigMap mounted
at `/app/acc-role.yaml`). For per-role behaviour, rely on the embedded `roles/`
tree (the operator delivers `roles/<role>/role.yaml` verbatim) — so a role whose
`role.yaml` sets `memory_reflection: true` or `workspace_access: true` keeps
that on the operator path.

> `spec.roleDefinition` currently lacks `memory_retrieval` / `memory_reflection`
> fields and the acc-config template doesn't render it — until the follow-up,
> set behaviour via the embedded role.yaml + `extraEnv`.

## Autoscaling (operator-only)

```yaml
  scaling:
    enabled: true
    roleScaling:
      - { role: ingester, minReplicas: 1, maxReplicas: 10, natsConsumerLagThreshold: 10 }
```

Requires KEDA. This replaces the standalone `worker_pool` for elasticity.
