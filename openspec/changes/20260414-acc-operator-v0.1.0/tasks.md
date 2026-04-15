# Tasks: ACC Operator v0.1.0

Branch: `feature/ACC-4-operator-v0.1.0-initial-scaffold`

Phases follow the approved implementation plan. Each task maps to a commit `[N] type(operator/scope): summary`.

---

## Phase 1 — Scaffold (commit [3])

- [x] [3a] Create `operator/` directory structure
- [x] [3b] Write `operator/go.mod` (module path, Go 1.22, controller-runtime v0.18)
- [x] [3c] Write `operator/PROJECT` (operator-sdk project metadata)
- [x] [3d] Write `operator/Makefile` (standard operator-sdk targets)
- [x] [3e] Write `operator/cmd/main.go` (manager setup, scheme registration, leader election)

## Phase 2 — CRD Types (commit [4])

- [x] [4a] Write `operator/api/v1alpha1/common_types.go` (enums: AgentRole, DeployMode, LLMBackend, CorpusPhase, etc.)
- [x] [4b] Write `operator/api/v1alpha1/agentcorpus_types.go` (full AgentCorpusSpec + Status + all sub-structs)
- [x] [4c] Write `operator/api/v1alpha1/agentcollective_types.go` (AgentCollectiveSpec + Status)
- [x] [4d] Write `operator/api/v1alpha1/groupversion_info.go`
- [x] [4e] Write `operator/api/v1alpha1/agentcorpus_webhook.go` (defaulting + validation webhooks)
- [x] [4f] Write `operator/config/crd/bases/acc.redhat.io_agentcorpora.yaml` (hand-written CRD YAML)
- [x] [4g] Write `operator/config/crd/bases/acc.redhat.io_agentcollectives.yaml`
- [x] [4h] Write `operator/config/samples/acc_v1alpha1_agentcorpus_standalone.yaml`
- [x] [4i] Write `operator/config/samples/acc_v1alpha1_agentcorpus_rhoai.yaml`
- [x] [4j] Write `operator/config/samples/acc_v1alpha1_agentcollective.yaml`

## Phase 3 — Util + Status Packages (commit [5])

- [x] [5a] Write `operator/internal/util/labels.go`
- [x] [5b] Write `operator/internal/util/discovery.go`
- [x] [5c] Write `operator/internal/util/resource.go`
- [x] [5d] Write `operator/internal/util/version.go`
- [x] [5e] Write `operator/internal/status/conditions.go`
- [x] [5f] Write `operator/internal/status/phase.go`
- [x] [5g] Write `operator/internal/status/writer.go`

## Phase 4 — Sub-Reconcilers (commit [6])

- [x] [6a] Write `operator/internal/reconcilers/interface.go`
- [x] [6b] Write `operator/internal/reconcilers/prerequisites.go`
- [x] [6c] Write `operator/internal/reconcilers/upgrade.go`
- [x] [6d] Write `operator/internal/reconcilers/infra/nats.go`
- [x] [6e] Write `operator/internal/reconcilers/infra/redis.go`
- [x] [6f] Write `operator/internal/reconcilers/infra/milvus.go`
- [x] [6g] Write `operator/internal/reconcilers/governance/opa_bundle_server.go`
- [x] [6h] Write `operator/internal/reconcilers/governance/gatekeeper.go`
- [x] [6i] Write `operator/internal/reconcilers/bridge/kafka_bridge.go`
- [x] [6j] Write `operator/internal/reconcilers/observability/otel_collector.go`
- [x] [6k] Write `operator/internal/reconcilers/observability/prometheus_rules.go`
- [x] [6l] Write `operator/internal/reconcilers/collective/collective.go`
- [x] [6m] Write `operator/internal/reconcilers/collective/agent_deployment.go`
- [x] [6n] Write `operator/internal/reconcilers/collective/keda_scaled_object.go`
- [x] [6o] Write `operator/internal/reconcilers/collective/kserve.go`

## Phase 5 — Templates + Main Controller (commit [7])

- [x] [7a] Write `operator/internal/templates/acc_config.go`
- [x] [7b] Write `operator/internal/templates/nats_config.go`
- [x] [7c] Write `operator/internal/templates/otel_config.go`
- [x] [7d] Write `operator/internal/controller/agentcorpus_controller.go`
- [x] [7e] Write `operator/internal/controller/agentcollective_controller.go`

## Phase 6 — RBAC + Config (commit [8])

- [x] [8a] Write `operator/config/rbac/role.yaml` (ClusterRole)
- [x] [8b] Write `operator/config/rbac/role_binding.yaml`
- [x] [8c] Write `operator/config/rbac/service_account.yaml`
- [x] [8d] Write `operator/config/manager/manager.yaml`
- [x] [8e] Write `operator/config/default/kustomization.yaml`
- [x] [8f] Write `operator/config/manager/kustomization.yaml`
- [x] [8g] Write `operator/config/rbac/kustomization.yaml`

## Phase 7 — OLM Bundle (commit [9])

- [x] [9a] Write `operator/bundle/manifests/acc-operator.clusterserviceversion.yaml`
- [x] [9b] Write `operator/bundle/metadata/annotations.yaml`
- [x] [9c] Write `operator/bundle/tests/scorecard/config.yaml`
- [x] [9d] Write `operator/bundle.Dockerfile`

## Phase 8 — Tests (commit [10])

- [x] [10a] Write `operator/test/unit/prerequisites_test.go`
- [x] [10b] Write `operator/test/unit/upgrade_test.go`
- [x] [10c] Write `operator/test/unit/phase_test.go`
- [x] [10d] Write `operator/test/unit/templates_test.go`

## Phase 9 — Final commit

- [ ] Update `docs/CHANGELOG.md` with operator entry
- [ ] `git tag operator/v0.1.0`
