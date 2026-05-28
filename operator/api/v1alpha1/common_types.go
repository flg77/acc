// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package v1alpha1

// DeployMode selects the infrastructure profile.
// Mirrors the Python acc/config.py DeployMode literal.
// +kubebuilder:validation:Enum=standalone;rhoai;edge
type DeployMode string

const (
	// DeployModeStandalone provisions NATS + Redis + LanceDB (local developer / CI / Podman).
	DeployModeStandalone DeployMode = "standalone"
	// DeployModeRHOAI provisions NATS + Redis + Milvus and expects
	// KServe and RHOAI operators to be installed on the cluster.
	DeployModeRHOAI DeployMode = "rhoai"
	// DeployModeEdge provisions NATS (leaf node) + Redis (single-node, eviction)
	// + LanceDB on local NVMe for MicroShift / K3s / production edge deployments.
	// KEDA, Gatekeeper, OTel Collector, and PrometheusRules are skipped.
	// Agents connect to a datacenter hub via NATS leaf node when the network is available.
	DeployModeEdge DeployMode = "edge"
)

// AgentRole identifies an ACC agent function.
//
// Schema-level validation is a regex on a DNS-label-style string;
// semantic validation against the operator's compiled-in catalogue happens
// in the AgentCollective validating webhook. New personas can be added by
// dropping a roles/<name>/role.yaml into the source tree and running
// `go generate ./...` against operator/internal/rolecatalogue/.
// +kubebuilder:validation:Pattern=`^[a-z][a-z0-9_]{1,62}$`
// +kubebuilder:validation:MinLength=2
// +kubebuilder:validation:MaxLength=63
type AgentRole string

const (
	// Legacy ACCv3 5-role set — kept for backwards compatibility with
	// existing AgentCorpus / AgentCollective custom resources.
	RoleIngester    AgentRole = "ingester"
	RoleAnalyst     AgentRole = "analyst"
	RoleSynthesizer AgentRole = "synthesizer"
	RoleArbiter     AgentRole = "arbiter"
	RoleObserver    AgentRole = "observer"

	// Coding-split-skills personas (D3 / PR #44, #39).
	// Used by examples/coding_split_skills/.
	RoleCodingAgent       AgentRole = "coding_agent"
	RoleCodingArchitect   AgentRole = "coding_agent_architect"
	RoleCodingDependency  AgentRole = "coding_agent_dependency"
	RoleCodingImplementer AgentRole = "coding_agent_implementer"
	RoleCodingReviewer    AgentRole = "coding_agent_reviewer"
	RoleCodingTester      AgentRole = "coding_agent_tester"

	// Autoresearcher personas (E4 / PR #44).
	// Used by examples/acc_autoresearcher/.
	RoleResearchPlanner     AgentRole = "research_planner"
	RoleResearchStrategist  AgentRole = "research_strategist"
	RoleResearchEconomist   AgentRole = "research_economist"
	RoleResearchCompetitor  AgentRole = "research_competitor"
	RoleResearchSynthesizer AgentRole = "research_synthesizer"
	RoleResearchCritic      AgentRole = "research_critic"
)

// LLMBackend selects the language model implementation.
// Mirrors the Python LLMBackendChoice literal.
// +kubebuilder:validation:Enum=ollama;anthropic;vllm;llama_stack
type LLMBackend string

const (
	LLMBackendOllama     LLMBackend = "ollama"
	LLMBackendAnthropic  LLMBackend = "anthropic"
	LLMBackendVLLM       LLMBackend = "vllm"
	LLMBackendLlamaStack LLMBackend = "llama_stack"
)

// MetricsBackend selects the telemetry backend.
// Mirrors the Python MetricsBackendChoice literal.
// +kubebuilder:validation:Enum=log;otel
type MetricsBackend string

const (
	MetricsBackendLog  MetricsBackend = "log"
	MetricsBackendOTel MetricsBackend = "otel"
)

// CorpusPhase represents the top-level operational phase of an AgentCorpus.
// +kubebuilder:validation:Enum=Pending;Progressing;Ready;Degraded;Error;UpgradeApprovalPending
type CorpusPhase string

const (
	CorpusPhasePending                CorpusPhase = "Pending"
	CorpusPhaseProgressing            CorpusPhase = "Progressing"
	CorpusPhaseReady                  CorpusPhase = "Ready"
	CorpusPhaseDegraded               CorpusPhase = "Degraded"
	CorpusPhaseError                  CorpusPhase = "Error"
	CorpusPhaseUpgradeApprovalPending CorpusPhase = "UpgradeApprovalPending"
)

// CollectivePhase represents the operational phase of a single AgentCollective.
// +kubebuilder:validation:Enum=Pending;Progressing;Ready;Degraded
type CollectivePhase string

const (
	CollectivePhasePending     CollectivePhase = "Pending"
	CollectivePhaseProgressing CollectivePhase = "Progressing"
	CollectivePhaseReady       CollectivePhase = "Ready"
	CollectivePhaseDegraded    CollectivePhase = "Degraded"
)

// UpgradeMode controls the operator upgrade strategy.
// +kubebuilder:validation:Enum=auto;manual
type UpgradeMode string

const (
	UpgradeModeAuto   UpgradeMode = "auto"
	UpgradeModeManual UpgradeMode = "manual"
)

// Condition type constants used in status.conditions[].type.
const (
	ConditionTypeReady                  = "Ready"
	ConditionTypeInfrastructureReady    = "InfrastructureReady"
	ConditionTypeCollectivesReady       = "CollectivesReady"
	ConditionTypePrerequisitesMet       = "PrerequisitesMet"
	ConditionTypeKafkaBridgeReady       = "KafkaBridgeReady"
	ConditionTypeUpgradeApprovalPending = "UpgradeApprovalPending"
	// ConditionTypeNATSLeafConnected is True when the edge NATS leaf node has
	// successfully connected to the datacenter hub (deployMode=edge only).
	// Unknown when hub_url is not configured or connectivity cannot be determined.
	ConditionTypeNATSLeafConnected = "NATSLeafConnected"
)

// Annotation keys used by the operator.
const (
	// AnnotationApproveUpgrade is applied by the user to approve an upgrade.
	// Value must equal the pending upgrade version string.
	AnnotationApproveUpgrade = "acc.redhat.io/approve-upgrade"
	// AnnotationPausedBy records who paused reconciliation.
	AnnotationPausedBy = "acc.redhat.io/paused-by"
)

// Label keys applied to all operator-managed resources.
const (
	LabelManagedBy    = "app.kubernetes.io/managed-by"
	LabelComponent    = "app.kubernetes.io/component"
	LabelVersion      = "app.kubernetes.io/version"
	LabelCollectiveID = "acc.redhat.io/collective-id"
	LabelAgentRole    = "acc.redhat.io/agent-role"
	LabelCorpusName   = "acc.redhat.io/corpus-name"
	LabelManagedByVal = "acc-operator"

	// LabelKagentiType is the discovery label Kagenti's operator watches on
	// workloads (OpenSpec 20260527-agentcard-discovery, Phase 1).  Applied to
	// an agent Deployment's ObjectMeta + pod-template labels (NOT its
	// selector — selector labels are immutable) when
	// AgentCollectiveSpec.Kagenti.Enabled is true.  See docs/kagenti-discovery.md.
	LabelKagentiType = "kagenti.io/type"
	// LabelKagentiTypeAgent is the value Kagenti uses for the agent workload
	// type (the auto-discovery match).
	LabelKagentiTypeAgent = "agent"
)
