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
// +kubebuilder:validation:Enum=standalone;rhoai
type DeployMode string

const (
	// DeployModeStandalone provisions NATS + Redis + LanceDB (edge/standalone Podman).
	DeployModeStandalone DeployMode = "standalone"
	// DeployModeRHOAI provisions NATS + Redis + Milvus and expects
	// KServe and RHOAI operators to be installed on the cluster.
	DeployModeRHOAI DeployMode = "rhoai"
)

// AgentRole identifies an ACC agent function.
// Mirrors the Python AgentRole literal. Full 5-role spec from ACCv3.
// +kubebuilder:validation:Enum=ingester;analyst;synthesizer;arbiter;observer
type AgentRole string

const (
	RoleIngester    AgentRole = "ingester"
	RoleAnalyst     AgentRole = "analyst"
	RoleSynthesizer AgentRole = "synthesizer"
	RoleArbiter     AgentRole = "arbiter"
	RoleObserver    AgentRole = "observer"
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
)
