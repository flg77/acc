# Proposal: Operator Feature Parity — Land D & E Epics on Kubernetes

| Field      | Value                                                    |
|------------|----------------------------------------------------------|
| Change ID  | 20260508-operator-feature-parity-d-e                     |
| Date       | 2026-05-08                                               |
| Status     | Draft                                                    |
| PR slots   | #48 → #51 (renumber at branch creation if drift)         |
| Depends on | D1–D6 (coding-split-skills, PRs #34–#40), E1–E6 (autoresearcher, PRs #41–#46) |

---

## Problem Statement

The repo has two deployment paths:

1. **Podman Compose** (`./acc-deploy.sh up` → `container/production/podman-compose.yml`) —
   works end-to-end including everything from PRs #26–#46 (cluster_id propagation, role-md
   authoring, sub-cluster estimator, slash commands, autoresearcher MCPs, split coding/
   research personas).

2. **Kubernetes operator** (`make install && make deploy` from `operator/`) — frozen at the
   original ACCv3 5-role design. None of the new features landed here.

The `examples/acc_autoresearcher/` and `examples/coding_split_skills/` showcases run only
via Podman Compose. They cannot be deployed on Kubernetes via the operator.

## Current Behavior (audited against source)

1. **`AgentRole` enum is locked to 5 legacy roles**.
   `operator/api/v1alpha1/common_types.go:32` and `agentcollective_types.go:97` constrain
   `agents[].role` to `[ingester, analyst, synthesizer, arbiter, observer]`. Same in the
   generated CRD `operator/config/crd/bases/acc.redhat.io_agentcollectives.yaml:126-132,
   278-284`. The API server will reject all 11 new personas at admission:
   `coding_agent_{architect,dependency,implementer,reviewer,tester}` (D3) and
   `research_{planner,strategist,economist,competitor,synthesizer,critic}` (E4). Even the
   umbrella `coding_agent` used by `container/production/podman-compose.yml:212` is rejected.

2. **`roles/`, `skills/`, `mcps/` are not delivered to agent pods**.
   `deploy/Containerfile.agent-core:57` copies only `acc/` + `acc-config.yaml`.
   `operator/internal/reconcilers/collective/agent_deployment.go:147-189` mounts only
   `acc-config`, `wasm-governance`, and a tiny inline `acc-role` ConfigMap rendered from
   `spec.roleDefinition` (purpose / persona / taskTypes / seedContext / allowedActions /
   categoryBOverrides / version — too narrow for full markdown roles with estimator /
   default_skills / allowed_mcps / system_prompt). Compare to compose's
   `../../roles:/app/roles:ro,z` bind mounts at lines 222, 251, 280.

3. **No MCP-server reconciler**. `grep -i 'skill\|mcp' operator/` returns empty. The three
   E2 MCPs (`web_browser_harness`, `web_search_brave`, `web_fetch`) and the diagnostic
   `echo_server` exist only as compose services under `--profile acc-autoresearcher` /
   `--profile mcp-echo`.

4. **CSV `alm-examples` and sample manifests are stale**.
   `operator/bundle/manifests/acc-operator.clusterserviceversion.yaml:42-46` and both
   `operator/config/samples/*.yaml` only reference legacy 5 roles.

5. **TUI sample doesn't mount roles/**.
   `operator/config/samples/acc_tui_deployment.yaml:36-78` doesn't set `ACC_ROLES_ROOT` or
   mount `roles/`, while `container/production/podman-compose.yml:464` does. The cluster-
   topology panel and markdown-role tooling will degrade.

6. **Compile bug**: `operator/api/v1alpha1/agentcollective_types.go:91-94` — the
   `RoleDefinition` struct is missing its closing `}` before `AgentRoleSpec` begins.
   `zz_generated.deepcopy.go` was generated from a previously correct version, so a fresh
   `make generate` would fail.

## Desired Behavior

After this change, an operator can:

- `kubectl apply -f operator/config/samples/acc_v1alpha1_agentcorpus_autoresearcher.yaml`
  and have the autoresearcher demo come up end-to-end (6 research personas + 3 MCP servers)
- `kubectl apply -f operator/config/samples/acc_v1alpha1_agentcorpus_coding_split.yaml`
  and have the coding-split-skills demo come up end-to-end (5 coding personas + echo MCP)
- Use any role name found in `roles/` without the API server rejecting the manifest;
  unknown names get a webhook error listing the closest matches.
- Run the legacy 5-role samples (`sol-corpus`, `rhoai-corpus`) **unchanged** — every change
  is additive or a strict loosening.

## Non-Goals

- No edits to `container/production/podman-compose.yml` or `acc-deploy.sh`.
- No Python agent code changes — env-var contract (`ACC_ROLES_ROOT`,
  `ACC_SKILLS_ROOT`, `ACC_MCPS_ROOT`) already in place
  (`acc/skills/registry.py:39-45`, `acc/mcp/registry.py:42-46`).
- No new CRD kinds (e.g. `RoleCatalogue`, `MCPServer`) — kept inline in `AgentCorpusSpec`.
- No CSV graph rewrite — clean `replaces:` v0.1.0 → v0.2.0 for OLM upgrade.
