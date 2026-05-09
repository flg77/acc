# Tasks: Operator Feature Parity — Land D & E Epics on Kubernetes

> **Lock protocol**: Before starting any task, edit its `claimed by:` slot to your
> hostname/initials, commit, and push **immediately** in a single-file commit. The push is
> the lock acquisition — the other instance will see it on its next `git pull`. Mark
> `- [x]` when the change lands on the PR branch.
>
> Branch naming: `feat/op-pr<NN>-<short-slug>` (renumber if PR drift on GitHub).
> Open each PR as a **draft** on first push.

---

## PR-49 — API & CRD foundations

**Branch**: `feat/op-pr49-api-foundations`
**PR**: https://github.com/flg77/acc/pull/49 (draft → ready 2026-05-09)
**PR slot claimed by**: `acc1` (host 10.199.12.91 — Claude instance)
**Status**: `ready-for-review` since 2026-05-09

- [x] **Fix the `}` bug** in `operator/api/v1alpha1/agentcollective_types.go:91-94`
  (`RoleDefinition` struct missing closing brace before `AgentRoleSpec`). Landed in
  commit `466273b`. *— claimed by: acc1*

- [x] **Loosen the role enum** in `operator/api/v1alpha1/common_types.go`: replaced
  closed `Enum=` with `Pattern=^[a-z][a-z0-9_]{1,62}$` + length bounds; dropped
  field-level `Enum=` markers on `AgentRoleSpec.Role` and `RoleScalingSpec.Role`.
  Landed in commit `9052391`. *— claimed by: acc1*

- [x] **Append exported role consts** in `common_types.go` for 12 personas (5
  coding-split + 6 research + umbrella `RoleCodingAgent`). Landed in commit
  `9052391`. *— claimed by: acc1*

- [x] **Add `MCPServerSpec` + status types** in `agentcorpus_types.go` —
  `Name/Image/Replicas/Port/Env/SecretEnv/ShmSizeMi/Resources` + matching status type
  + `Status.MCPServerStatuses` map. Landed in commit `d9c2784`. *— claimed by: acc1*

- [x] **Add `ManifestDelivery` field** — `Enum=all;none`, default `all`. Landed in
  commit `d9c2784`. *— claimed by: acc1*

- [x] **Implement role catalogue** at `operator/internal/rolecatalogue/`. Public API:
  `IsKnown / All / Suggest`. Source via `//go:embed known_roles.txt`; generator at
  `operator/hack/gen-catalogue.go` triggered by `//go:generate`. 47 roles seeded.
  Landed in commit `1f7c3d…` *(pre-rebase hash, see git log)*. *— claimed by: acc1*

- [x] **Add `AgentCollective` validating webhook** at `agentcollective_webhook.go`
  with closest-match Levenshtein suggestions; bonus validation for
  `roleScaling[*].role` declared-in-agents check, minReplicas≤maxReplicas, and
  llm sub-struct presence. Also extended `agentcorpus_webhook.go`: defaults
  `manifestDelivery=all`, defaults `MCPServer.Replicas=1` and `Port=8080`,
  rejects duplicate MCP server names. Landed in commit `d2043d0`.
  *— claimed by: acc1*

- [x] **Run `make manifests generate`** — regenerated `zz_generated.deepcopy.go`
  (+632/-238), `config/crd/bases/acc.redhat.io_agent{collectives,corpora}.yaml`
  (the role enum opens up; the new MCP/manifest fields appear), and
  `config/webhook/manifests.yaml` (AgentCollective mutating + validating webhooks
  registered). Landed in commit `1d61a17`. Includes a build-hygiene side commit
  `24e5414` that repaired a stale kube-openapi pseudo-version in `go.mod` and
  generated a missing `go.sum` so `go vet/build/test` and `make generate` could
  run at all. *— claimed by: acc1*

- [x] **Unit tests** at `operator/test/unit/role_catalogue_test.go` — 12 test
  functions covering catalogue membership for legacy + new personas, sorted /
  unique / mutation-isolated `All()`, Suggest typo recognition for 6 realistic
  inputs, n-cap, n≤0 contract, and distance cutoff. All pass; full suite clean.
  Landed in commit `b325344`. *— claimed by: acc1*

- [x] **Verify backwards compat** — `go vet ./...`, `go build ./...`,
  `go test ./test/unit/...` all clean. Regex `^[a-z][a-z0-9_]{1,62}$` accepts
  every role in both legacy samples (`standalone` + `rhoai`) and every new
  persona const. Live `kubectl apply` skipped in favour of regex verification
  to avoid mutating the shared cluster — see PR #49 description.
  *— claimed by: acc1*

---

## PR-50 — Manifest delivery reconciler

**Branch**: `feat/op-pr50-manifest-delivery`
**PR slot claimed by**: `acc1` (host 10.199.12.91 — Claude instance)
**Status**: `in-progress` since 2026-05-09
**Blocks on**: PR-49 merged ✅ (uses the new `ManifestDelivery` field)

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

## PR-51 — MCP server reconciler

**Branch suggestion**: `feat/op-pr51-mcp-reconciler`
**PR slot claimed by**: `-`
**Status**: `unstarted`
**Blocks on**: PR-49 merged (uses `MCPServerSpec`)

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

## PR-52 — Demo samples + CSV update

**Branch suggestion**: `feat/op-pr52-demo-samples`
**PR slot claimed by**: `-`
**Status**: `unstarted`
**Blocks on**: PR-49, PR-50, PR-51 merged

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

Not blocking the main sequence; can run in parallel with PR-50 or PR-51.

- [ ] Add `jsonschema` to `Containerfile.agent-core` (`microdnf install`) so role/skill
  schema validation is strict in cluster — `acc/skills/registry.py:52-64` falls back
  silently when missing. *— claimed by: -*
