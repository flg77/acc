// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// AgentCollectiveSpec defines a single collective within the corpus.
// +kubebuilder:object:generate=true
type AgentCollectiveSpec struct {
	// CollectiveID is the logical identifier used in NATS subjects
	// (acc.{collectiveID}.{signal_type}) and in agent labels.
	// Must be DNS-label-safe.
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$`
	CollectiveID string `json:"collectiveId"`

	// CorpusRef names the AgentCorpus that owns this collective.
	// Set automatically by the AgentCorpus reconciler; do not set manually.
	CorpusRef corev1.LocalObjectReference `json:"corpusRef"`

	// Agents lists the role-specific deployment configurations.
	// +kubebuilder:validation:MinItems=1
	Agents []AgentRoleSpec `json:"agents"`

	// LLM configures the language model backend for this collective.
	LLM LLMSpec `json:"llm"`

	// Scaling configures KEDA-based autoscaling per role.
	// Requires KEDA to be installed cluster-wide (operator checks via API discovery).
	// +optional
	Scaling *ScalingSpec `json:"scaling,omitempty"`

	// HeartbeatIntervalSeconds sets the HEARTBEAT emission interval for all agents.
	// Matches acc-config.yaml agent.heartbeat_interval_s.
	// +kubebuilder:validation:Minimum=5
	// +kubebuilder:validation:Maximum=300
	// +kubebuilder:default=30
	// +optional
	HeartbeatIntervalSeconds int32 `json:"heartbeatIntervalSeconds,omitempty"`

	// RoleDefinition sets the cognitive role for all agents in this collective.
	// The reconciler renders this into a ConfigMap named acc-role-{collectiveId}
	// mounted read-only into every agent pod at /app/acc-role.yaml.
	// Agents load this as the highest-priority role source on startup.
	// +optional
	RoleDefinition *RoleDefinition `json:"roleDefinition,omitempty"`
}

// RoleDefinition mirrors RoleDefinitionConfig from acc/config.py.
// All fields are optional; omitted fields use the agent's compiled defaults.
type RoleDefinition struct {
	// Purpose is the agent's primary objective statement, injected as the
	// first component of the CognitiveCore system prompt.
	// +optional
	Purpose string `json:"purpose,omitempty"`

	// Persona controls the LLM response style.
	// +kubebuilder:validation:Enum=concise;formal;exploratory;analytical
	// +kubebuilder:default=concise
	// +optional
	Persona string `json:"persona,omitempty"`

	// TaskTypes lists the NATS signal types this agent will accept.
	// +optional
	TaskTypes []string `json:"taskTypes,omitempty"`

	// SeedContext is a domain-specific priming string injected into every LLM call.
	// +optional
	SeedContext string `json:"seedContext,omitempty"`

	// AllowedActions lists the actions the agent may perform.
	// +optional
	AllowedActions []string `json:"allowedActions,omitempty"`

	// CategoryBOverrides are live-updatable Cat-B governance setpoints
	// (e.g. token_budget, rate_limit_rpm).
	// +optional
	CategoryBOverrides map[string]string `json:"categoryBOverrides,omitempty"`

	// Version is the semantic version of this role definition.
	// +kubebuilder:default="0.1.0"
	// +optional
	Version string `json:"version,omitempty"`

// AgentRoleSpec defines the deployment configuration for one agent role.
type AgentRoleSpec struct {
	// Role identifies the ACC agent role.
	// +kubebuilder:validation:Enum=ingester;analyst;synthesizer;arbiter;observer
	Role AgentRole `json:"role"`

	// Replicas is the baseline replica count (before KEDA scaling).
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	// +kubebuilder:default=1
	// +optional
	Replicas int32 `json:"replicas,omitempty"`

	// Resources sets the CPU/memory requests and limits for agent pods.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`

	// ExtraEnv injects additional environment variables into agent pods.
	// +optional
	ExtraEnv []corev1.EnvVar `json:"extraEnv,omitempty"`

	// VolumeClaimTemplates allows attaching role-specific PVCs.
	// +optional
	VolumeClaimTemplates []corev1.PersistentVolumeClaimTemplate `json:"volumeClaimTemplates,omitempty"`
}

// LLMSpec configures the LLM backend for a collective.
type LLMSpec struct {
	// Backend selects the LLM implementation.
	// +kubebuilder:validation:Enum=ollama;anthropic;vllm;llama_stack
	Backend LLMBackend `json:"backend"`

	// Ollama configures the Ollama REST backend (standalone mode).
	// +optional
	Ollama *OllamaSpec `json:"ollama,omitempty"`

	// Anthropic configures the Anthropic Claude API backend.
	// +optional
	Anthropic *AnthropicSpec `json:"anthropic,omitempty"`

	// VLLM configures the KServe InferenceService backend (rhoai mode).
	// +optional
	VLLM *VLLMSpec `json:"vllm,omitempty"`

	// LlamaStack configures the Llama Stack inference API (rhoai mode).
	// +optional
	LlamaStack *LlamaStackSpec `json:"llamaStack,omitempty"`

	// EmbeddingModel is the sentence-transformers model for local embedding fallback.
	// +kubebuilder:default="all-MiniLM-L6-v2"
	// +optional
	EmbeddingModel string `json:"embeddingModel,omitempty"`
}

// OllamaSpec configures Ollama as the LLM backend.
type OllamaSpec struct {
	// BaseURL is the Ollama REST API endpoint.
	// +kubebuilder:default="http://localhost:11434"
	BaseURL string `json:"baseUrl"`

	// Model is the Ollama model identifier.
	// +kubebuilder:default="llama3.2:3b"
	Model string `json:"model"`
}

// AnthropicSpec configures Anthropic Claude as the LLM backend.
type AnthropicSpec struct {
	// Model is the Anthropic model identifier.
	// +kubebuilder:default="claude-sonnet-4-6"
	Model string `json:"model"`

	// APIKeySecretRef references a Secret with an ACC_ANTHROPIC_API_KEY key.
	APIKeySecretRef corev1.SecretKeySelector `json:"apiKeySecretRef"`
}

// VLLMSpec configures a KServe InferenceService as the LLM backend.
type VLLMSpec struct {
	// InferenceServiceRef names the KServe InferenceService in the same namespace.
	// The operator reads the InferenceService URL from its status.
	InferenceServiceRef string `json:"inferenceServiceRef"`

	// Model is the vLLM model identifier.
	Model string `json:"model"`

	// Deploy controls whether the operator creates the InferenceService.
	// Set to false to manage the InferenceService separately.
	// +kubebuilder:default=true
	// +optional
	Deploy bool `json:"deploy,omitempty"`

	// ModelStoragePVC is the PVC name containing the model weights.
	// Required when deploy=true.
	// +optional
	ModelStoragePVC string `json:"modelStoragePVC,omitempty"`

	// Resources for the InferenceService predictor pod.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
}

// LlamaStackSpec configures Llama Stack as the LLM backend.
type LlamaStackSpec struct {
	// BaseURL is the Llama Stack distribution endpoint.
	BaseURL string `json:"baseUrl"`

	// ModelID is the Llama Stack model identifier.
	ModelID string `json:"modelId"`
}

// ScalingSpec configures KEDA autoscaling for agent roles.
type ScalingSpec struct {
	// Enabled activates KEDA ScaledObjects for all roles in this collective.
	// Requires KEDA to be installed cluster-wide.
	// +kubebuilder:default=false
	Enabled bool `json:"enabled"`

	// RoleScaling allows per-role scaling overrides.
	// +optional
	RoleScaling []RoleScalingSpec `json:"roleScaling,omitempty"`
}

// RoleScalingSpec configures KEDA scaling for a single agent role.
type RoleScalingSpec struct {
	// Role identifies which agent role this scaling config applies to.
	// +kubebuilder:validation:Enum=ingester;analyst;synthesizer;arbiter;observer
	Role AgentRole `json:"role"`

	// MinReplicas is the KEDA minimum replica count.
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:default=1
	// +optional
	MinReplicas int32 `json:"minReplicas,omitempty"`

	// MaxReplicas is the KEDA maximum replica count.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:default=10
	// +optional
	MaxReplicas int32 `json:"maxReplicas,omitempty"`

	// NATSConsumerLagThreshold scales up when NATS consumer lag exceeds this value.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:default=10
	// +optional
	NATSConsumerLagThreshold int64 `json:"natsConsumerLagThreshold,omitempty"`

	// HealthMetricThreshold scales down when the role's average health_score
	// drops below this value (percentage 0-100).
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	// +kubebuilder:default=70
	// +optional
	HealthMetricThreshold int32 `json:"healthMetricThreshold,omitempty"`
}

// -----------------------------------------------------------------------
// Status
// -----------------------------------------------------------------------

// AgentCollectiveStatus reports the observed state of one collective.
type AgentCollectiveStatus struct {
	// Phase is the collective's operational state.
	// +optional
	Phase CollectivePhase `json:"phase,omitempty"`

	// ObservedGeneration is the .metadata.generation this status reflects.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions contains the condition set for this collective.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// ReadyAgents maps role → number of ready pods.
	// +optional
	ReadyAgents map[string]int32 `json:"readyAgents,omitempty"`

	// DesiredAgents maps role → desired replica count.
	// +optional
	DesiredAgents map[string]int32 `json:"desiredAgents,omitempty"`

	// ScaledObjectsActive is true when KEDA ScaledObjects are in effect.
	// +optional
	ScaledObjectsActive bool `json:"scaledObjectsActive,omitempty"`

	// KServeReady is true when the InferenceService is in ready state.
	// +optional
	KServeReady bool `json:"kserveReady,omitempty"`
}

// -----------------------------------------------------------------------
// AgentCollective resource
// -----------------------------------------------------------------------

// AgentCollective represents a single collective within an AgentCorpus.
// It is namespace-scoped and owned by its parent AgentCorpus.
//
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced,shortName=acol,categories=acc
// +kubebuilder:printcolumn:name="CollectiveID",type="string",JSONPath=".spec.collectiveId"
// +kubebuilder:printcolumn:name="LLM",type="string",JSONPath=".spec.llm.backend"
// +kubebuilder:printcolumn:name="Phase",type="string",JSONPath=".status.phase"
// +kubebuilder:printcolumn:name="Age",type="date",JSONPath=".metadata.creationTimestamp"
type AgentCollective struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   AgentCollectiveSpec   `json:"spec,omitempty"`
	Status AgentCollectiveStatus `json:"status,omitempty"`
}

// AgentCollectiveList contains a list of AgentCollective.
// +kubebuilder:object:root=true
type AgentCollectiveList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []AgentCollective `json:"items"`
}

func init() {
	SchemeBuilder.Register(&AgentCollective{}, &AgentCollectiveList{})
}
