# Proposal: ACC Operator v0.1.0 — OpenShift/Kubernetes Operator

| Field       | Value                                                      |
|-------------|------------------------------------------------------------|
| Change ID   | ACC-4                                                      |
| Date        | 2026-04-14                                                 |
| Status      | Approved                                                   |
| Author      | Michael                                                    |
| Branch      | `feature/ACC-4-operator-v0.1.0-initial-scaffold`           |
| Ticket      | ACC-4                                                      |

---

## Problem Statement

The ACC framework (Python package, completed in Phase 1a) runs as standalone Podman containers
(solarSys) or manually deployed Kubernetes resources. There is no automated lifecycle manager for
OpenShift or Kubernetes clusters — users must hand-apply YAML, track upgrade sequences, and
manually verify prerequisites like Kafka, KEDA, or RHOAI.

## Current Behavior

- ACC is deployed via `podman-compose.yml` on solarSys (standalone mode)
- No Kubernetes operator exists
- No automated prerequisite checking
- Upgrading NATS or Redis requires manual StatefulSet edits
- No OLM integration for OperatorHub distribution

## Desired Behavior

A Kubernetes Operator (`acc-operator`) that:

1. Installs the full ACC framework via a single `kubectl apply -f agentcorpus.yaml`
2. Manages NATS, Redis, OPA bundle server, NATS-Kafka bridge, OTel collector, and agent Deployments
3. Does **not** install Kafka — probes for its existence and warns via Events + Status conditions
4. Degrades gracefully when KEDA, OPA Gatekeeper, or RHOAI are absent (Warning events, skip those resources)
5. Gates shared infrastructure upgrades (NATS, Redis) behind user approval when `upgradePolicy.requireApproval=true`
6. Is installable via OperatorHub (OLM bundle, ClusterServiceVersion)
7. Supports both `standalone` (edge, LanceDB) and `rhoai` (datacenter, Milvus + KServe) deploy modes

## Success Criteria

- [ ] `make build` compiles the operator binary without errors
- [ ] `make generate && make manifests` regenerates CRD YAML cleanly (no drift)
- [ ] `make test` passes all unit tests (prerequisites, upgrade flow, phase computation, template rendering)
- [ ] `make bundle && operator-sdk bundle validate ./bundle` passes
- [ ] envtest: `AgentCorpus` in standalone mode → NATS + Redis + agent Deployments created
- [ ] envtest: `requireApproval=true` + version bump → `UpgradeApprovalPending` condition, no Deployment update until annotation applied
- [ ] envtest: `spec.kafka` set without Kafka running → `KafkaBridgeReady=False` Warning, corpus phase `Degraded` (not `Error`)
- [ ] Template test: rendered `acc-config.yaml` passes Python `ACCConfig.model_validate()` without errors

## Scope

### In Scope
- `AgentCorpus` and `AgentCollective` CRDs (v1alpha1)
- All sub-reconcilers: NATS, Redis, Milvus probe, OPA bundle server, Gatekeeper CTs, Kafka bridge, OTel, PrometheusRules, Agent Deployments, KEDA ScaledObjects, KServe InferenceService
- Prerequisite detection: Kafka (TCP), KEDA (API discovery), Gatekeeper (API discovery), RHOAI (API discovery)
- Upgrade approval gate (annotation-driven)
- OLM bundle with CSV
- OpenSpec change documents
- Unit tests (envtest + unit)

### Out of Scope
- Kafka installation (external dependency — documented only)
- KEDA operator installation
- OPA Gatekeeper installation
- RHOAI installation
- `AgentCell` sub-CRD (reserved for v1alpha2)
- Multi-namespace install mode
- Grafana dashboard CR creation (ConfigMap only)
- GUI / console plugin

## Assumptions

1. Target cluster runs OpenShift 4.14+ or Kubernetes 1.27+
2. `acc-agent-core` image is available at `registry.access.redhat.com/acc/acc-agent-core:{version}`
   (or an overridable registry)
3. Operator SDK 1.36+ (Go 1.22+) used for scaffolding
4. `controller-gen` v0.15+ available for CRD/RBAC generation
5. OLM is installed on target cluster for OperatorHub distribution
6. Kafka brokers, when used, are Red Hat Streams for Apache Kafka (AMQ Streams) compatible
7. Python `ACCConfig` schema in `acc/config.py` is the ground truth for `acc-config.yaml` rendering
