# Standalone ↔ operator parity — keeping dev from conflicting with the DC path

Two delivery paths share one code base + one agent image:

- **Standalone** (podman) — `acc/collective.py` `roles_to_compose` → compose env.
- **DC / RHOAI** (operator) — `operator/` CRDs → Deployments.

This doc is the contract that keeps fast standalone dev from silently breaking
the operator path, and the gap list from the recent feature work.

## What already keeps them in sync

| Shared artifact | Mechanism | Status |
|---|---|---|
| `roles/`, `skills/`, `mcps/` | `make sync-manifests` mirrors repo-root trees into the operator for `//go:embed`; delivered as ConfigMaps mounted in agent pods | ✅ in parity |
| agent image | both build `container/production/Containerfile.agent-core` | ✅ same image |
| `ACC_*` env contract | operator sets the same env the compose sets; `AgentRoleSpec.extraEnv` is the per-agent escape hatch mirroring `AgentSpec.extra_env` | ✅ mechanism exists |
| `collective_id`/role/replicas | 1:1 CRD fields | ✅ |

Because role behaviour lives in `role.yaml` (embedded verbatim) and per-agent
overrides ride `extraEnv`, **most recent features work on the operator today**
— just less ergonomically than via clean CRD fields.

## Gap table (recent PRs vs operator)

| Feature | Standalone | Operator today | Clean fix (tracked) |
|---|---|---|---|
| Per-agent model (PR-MM1/2/3) | `AgentSpec.model` + `models.yaml` | via `agents[].extraEnv` (ACC_LLM_*); **no `model` field** | add `AgentRoleSpec.model` + resolve |
| Memory reflection (PR-MEM2) | role flag `memory_reflection` + `ACC_REFLECTION_INTERVAL_S` | role flag flows via embedded `role.yaml`; interval via `extraEnv` | render in acc-config template; CRD field |
| Memory retrieval (PR-I) | role flag `memory_retrieval` | flows via embedded `role.yaml` (default on) | CRD field for inline override |
| Prompt cache (PR-CA2) | `ACC_LLM_ENABLE_PROMPT_CACHE` | via `extraEnv` | render in template |
| Compliance frameworks (PR-Z2) | `regulatory_layer/frameworks/*.yaml` read by gap-analysis | **not embedded** (`sync-manifests` only does roles/skills/mcps) | extend `sync-manifests` + delivery when running `compliance_officer` gap scans in-cluster |
| `models.yaml` (registry) | TUI dropdown + `roles_to_compose` (host-side) | not needed at runtime (operator resolves via LLMSpec/extraEnv) | n/a — authoring-only |
| `spec.roleDefinition` inline | — | present but **not rendered** by `acc_config.go` template | render it |

Severity: none are hard blockers (extraEnv + embedded role.yaml cover them).
The clean CRD fields + template rendering are ergonomics + discoverability.
The one genuine delivery gap is **frameworks not embedded** — only matters if a
`compliance_officer` agent runs `COMPLIANCE_GAP_SCAN` in-cluster.

## Closers (tracked follow-up workstream — not this round)

1. `AgentRoleSpec.model` (+ optional `modelsRegistry` ConfigMap) → reconciler
   resolves to the same env `acc.models.model_env` emits.
2. `RoleDefinition.memoryRetrieval` / `memoryReflection` fields +
   `operator/internal/templates/acc_config.go` rendering `roleDefinition`
   (today it renders collective LLM/governance/metrics only).
3. Extend `make sync-manifests` + `manifests/delivery.go` to embed
   `regulatory_layer/frameworks/` (+ `models.yaml` if ever runtime-needed).
4. **Python↔Go parity test** — extend `tests/test_collective_spec.py` to walk
   `operator/api/v1alpha1/agentcollective_types.go` struct tags and assert the
   standalone `AgentSpec`/`RoleDefinitionConfig` field sets agree (fail when a
   new standalone field has no operator counterpart).
5. **CI** — run the parity test + `make -C operator manifests generate` (no
   diff) + a `kustomize build gitops/...` lint on every PR.

## Process rule (the no-conflict contract)

> A standalone PR that adds an `AgentSpec` field, a `RoleDefinitionConfig`
> field, or a new `ACC_*` env var **must** either (a) be expressible via
> `extraEnv`/embedded `role.yaml` (note it in the PR), or (b) open a paired
> "operator parity" issue referencing this table. The parity test (closer #4)
> enforces (b) once it lands.

This lets standalone dev keep moving (podman is the fast loop) while the
operator path catches up deliberately — ops stay in `gitops/`, code stays in
`acc/` + `operator/`, and drift is visible, not silent.
