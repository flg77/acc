# Tasks: Operator Feature Parity — Land D & E Epics on Kubernetes

> **Lock protocol**: Before starting any task, edit its `claimed by:` slot to your
> hostname/initials, commit, and push **immediately** in a single-file commit. The push is
> the lock acquisition — the other instance will see it on its next `git pull`. Mark
> `- [x]` when the change lands on the PR branch.
>
> Branch naming: `feat/op-pr<NN>-<short-slug>` (renumber if PR drift on GitHub).
> Open each PR as a **draft** on first push.

---

## PR-48 — API & CRD foundations

**Branch suggestion**: `feat/op-pr48-api-foundations`
**PR slot claimed by**: `-`
**Status**: `unstarted`

- [ ] **Fix the `}` bug** in `operator/api/v1alpha1/agentcollective_types.go:91-94`
  (`RoleDefinition` struct missing closing brace before `AgentRoleSpec`). After the fix,
  `make generate` should be a no-op against the existing `zz_generated.deepcopy.go`,
  proving the source matches the generated shape. *— claimed by: -*

- [ ] **Loosen the role enum**. In `operator/api/v1alpha1/common_types.go` replace the
  `// +kubebuilder:validation:Enum=ingester;...` marker on `AgentRole` with
  `Pattern=^[a-z][a-z0-9_]{1,62}$` + `MinLength=2` + `MaxLength=63`. Drop the redundant
  field-level `Enum=` markers in `agentcollective_types.go` on `AgentRoleSpec.Role` and
  `RoleScalingSpec.Role`. *— claimed by: -*

- [ ] **Append exported role consts** in `common_types.go` for the 11 new personas plus
  `RoleCodingAgent` umbrella: `RoleCodingArchitect`, `RoleCodingDependency`,
  `RoleCodingImplementer`, `RoleCodingReviewer`, `RoleCodingTester`, `RoleResearchPlanner`,
  `RoleResearchStrategist`, `RoleResearchEconomist`, `RoleResearchCompetitor`,
  `RoleResearchSynthesizer`, `RoleResearchCritic`. *— claimed by: -*

- [ ] **Add `MCPServerSpec` + status types** in `operator/api/v1alpha1/agentcorpus_types.go`.
  Fields: `Name, Image, Replicas, Port, Env, SecretEnv, ShmSizeMi, Resources`. Status
  type: `MCPServerStatus{Ready bool; Replicas int32; ServiceURL string}`. Add
  `Status.MCPServerStatuses map[string]MCPServerStatus`. *— claimed by: -*

- [ ] **Add `ManifestDelivery` field** in `agentcorpus_types.go`:
  `// +kubebuilder:validation:Enum=all;none` + `// +kubebuilder:default=all`. *— claimed by: -*

- [ ] **Implement role catalogue**: create `operator/internal/rolecatalogue/catalogue.go`
  with `var KnownRoles map[string]struct{}` populated via `go:embed`-baked listing of
  `roles/*/role.yaml`. Add the generator at `operator/hack/gen-catalogue.go` triggered by
  `//go:generate`. *— claimed by: -*

- [ ] **Add `AgentCollective` validating webhook** at
  `operator/api/v1alpha1/agentcollective_webhook.go` (parallel to existing
  `agentcorpus_webhook.go`). Reject roles not in `KnownRoles` with closest-match
  suggestions. *— claimed by: -*

- [ ] **Run `make generate manifests`** and commit the regenerated
  `zz_generated.deepcopy.go` and `config/crd/bases/*.yaml` deltas. *— claimed by: -*

- [ ] **Unit tests** at `operator/test/unit/role_catalogue_test.go` covering catalogue
  membership and the closest-match suggestion path. *— claimed by: -*

- [ ] **Verify backwards compat**: `kubectl apply --dry-run=server -f
  config/samples/acc_v1alpha1_agentcorpus_standalone.yaml` succeeds; same for `_rhoai`.
  *— claimed by: -*

---

## PR-49 — Manifest delivery reconciler

**Branch suggestion**: `feat/op-pr49-manifest-delivery`
**PR slot claimed by**: `-`
**Status**: `unstarted`
**Blocks on**: PR-48 merged (uses the new `ManifestDelivery` field)

- [ ] **Implement `ManifestDeliveryReconciler`** at
  `operator/internal/reconcilers/manifests/delivery.go`. `embed.FS` over `roles/`,
  `skills/`, `mcps/`. Upsert three corpus-namespace ConfigMaps (`acc-roles`,
  `acc-skills`, `acc-mcps`) via `util.Upsert`. Keys flatten `/` to `__`; carry the
  `items[]` projection list alongside so the volume mount re-projects to slash-paths.
  *— claimed by: -*

- [ ] **Wire reconciler into chain**: `operator/internal/controller/agentcorpus_controller.go`
  — slot the new reconciler #2 (after `PrerequisiteReconciler`, before
  `UpgradeReconciler`). *— claimed by: -*

- [ ] **Inject volumes/env in agent pods**:
  `operator/internal/reconcilers/collective/agent_deployment.go`. Append three
  `VolumeMount`s (`/etc/acc/roles`, `/etc/acc/skills`, `/etc/acc/mcps`, all read-only),
  three `Volume`s referencing the corpus-scoped CMs with `items[]` projection, three env
  vars (`ACC_ROLES_ROOT`, `ACC_SKILLS_ROOT`, `ACC_MCPS_ROOT`). Gate on
  `corpus.Spec.ManifestDelivery != "none"`. *— claimed by: -*

- [ ] **TUI parity**: edit `operator/config/samples/acc_tui_deployment.yaml` to add the
  same three env vars and `acc-roles` / `acc-skills` volume mounts. *— claimed by: -*

- [ ] **Unit + envtest coverage**:
  - `manifest_delivery_test.go` with a 3-role embed.FS fixture.
  - Extend `agentcorpus_controller_test.go` to assert the legacy `sol-corpus` sample
    produces all three ConfigMaps with key counts equal to `find roles -type f | wc -l`,
    `find skills -type f | wc -l`, `find mcps -type f | wc -l`.
  *— claimed by: -*

- [ ] **Manual contract check**: `kubectl get cm acc-roles -o jsonpath='{.data}' | jq
  'keys|length'` equals `find roles -type f | wc -l`. *— claimed by: -*

---

## PR-50 — MCP server reconciler

**Branch suggestion**: `feat/op-pr50-mcp-reconciler`
**PR slot claimed by**: `-`
**Status**: `unstarted`
**Blocks on**: PR-48 merged (uses `MCPServerSpec`)

- [ ] **Implement `MCPServerReconciler`** at
  `operator/internal/reconcilers/mcp/server.go`. For each `corpus.Spec.MCPServers[i]`:
  - Upsert Deployment (image, replicas, env, securityContext UID 1001).
  - When `ShmSizeMi > 0`, add a `Memory`-medium `emptyDir` mounted at `/dev/shm` with
    `sizeLimit`.
  - Upsert Service named `acc-mcp-{name}` on `Port` (default 8080) — matches the `url:`
    field already in `mcps/*/mcp.yaml`.
  - Aggregate readiness into `corpus.Status.MCPServerStatuses[name]`.
  *— claimed by: -*

- [ ] **Wire into reconciler chain**:
  `operator/internal/controller/agentcorpus_controller.go` — slot between
  `KafkaBridgeReconciler` and `OTelCollectorReconciler`. *— claimed by: -*

- [ ] **Test**: `operator/internal/reconcilers/mcp/server_test.go` using
  `controller-runtime` `fake.Client`. Cover 3-MCP corpus including the browser-harness
  `shm_size` path. *— claimed by: -*

- [ ] **Envtest**: create a corpus with one MCP, assert Deployment + Service produced and
  status populated. *— claimed by: -*

---

## PR-51 — Demo samples + CSV update

**Branch suggestion**: `feat/op-pr51-demo-samples`
**PR slot claimed by**: `-`
**Status**: `unstarted`
**Blocks on**: PR-48, PR-49, PR-50 merged

- [ ] **Autoresearcher sample**:
  `operator/config/samples/acc_v1alpha1_agentcorpus_autoresearcher.yaml`. `AgentCorpus` +
  `AgentCollective` with `mcpServers: [web-search-brave, web-fetch, web-browser-harness]`
  (Brave key + Anthropic key from operator-supplied Secrets, `BROWSER_HARNESS_BACKEND=
  anthropic`, `shmSizeMi: 256`). 6 research personas matching
  `examples/acc_autoresearcher/expected_topology.md`. *— claimed by: -*

- [ ] **Coding-split sample**:
  `operator/config/samples/acc_v1alpha1_agentcorpus_coding_split.yaml`. 5 coding personas
  matching `examples/coding_split_skills/expected_topology.md`; `mcpServers:
  [echo-server]`. *— claimed by: -*

- [ ] **Update samples kustomization**: add the two new files to
  `operator/config/samples/kustomization.yaml`. *— claimed by: -*

- [ ] **CSV update**: `operator/bundle/manifests/acc-operator.clusterserviceversion.yaml`.
  Replace 2-element `alm-examples` with 6-element array (legacy `sol-corpus` + 4 new
  docs). Add a "Demos" subsection to the description. Bump CSV version to `v0.2.0` with
  `replaces: acc-operator.v0.1.0`. *— claimed by: -*

- [ ] **Kind smoke script**: `operator/hack/test-kind.sh`. Apply autoresearcher sample,
  wait for `Ready`, assert `kubectl exec ... -- ls /etc/acc/roles | grep research_planner`
  and `... ls /etc/acc/mcps | grep web_search_brave`. *— claimed by: -*

- [ ] **Bundle scorecard**: `make bundle && operator-sdk scorecard` runs clean against
  the new examples. *— claimed by: -*

---

## Optional — PR-A (parallel hardening)

Not blocking the main sequence; can run in parallel with PR-49 or PR-50.

- [ ] Add `jsonschema` to `Containerfile.agent-core` (`microdnf install`) so role/skill
  schema validation is strict in cluster — `acc/skills/registry.py:52-64` falls back
  silently when missing. *— claimed by: -*
