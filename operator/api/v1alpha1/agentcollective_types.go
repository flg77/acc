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

	// Spiffe configures SPIFFE workload identity for this collective
	// (proposal 011).  When enabled, the operator issues a matching
	// ClusterSPIFFEID custom resource so spire-controller-manager
	// attests the collective's agent pods.  Omitted / disabled keeps
	// the legacy Ed25519 trust model.
	// +optional
	Spiffe *SpiffeSpec `json:"spiffe,omitempty"`

	// Kagenti opts the collective in to Kagenti's AgentCard auto-discovery
	// (OpenSpec 20260527-agentcard-discovery, Phase 1).  When Enabled is
	// true, the operator stamps the label `kagenti.io/type: agent` on each
	// agent Deployment + pod so Kagenti's kagenti-operator auto-creates an
	// AgentCard CR.  Omitted / disabled is the default — existing
	// collectives are unaffected.
	// +optional
	Kagenti *KagentiSpec `json:"kagenti,omitempty"`

	// DisableAssistant opts the collective out of the default `assistant`
	// concierge.  Every collective ships an assistant by default — the
	// mutating webhook injects `assistant` into spec.agents when absent — so
	// there is always a governed entry point for onboarding, catalogue
	// queries and PROPOSE_INFUSE routing (proposal 023 §4b / 021 C3).  Set
	// true to suppress that injection (e.g. a minimal or special-purpose
	// collective that manages its own roster).  nil/false keeps the
	// assistant; an explicitly-declared assistant in spec.agents is never
	// overwritten regardless of this flag.
	// +optional
	DisableAssistant *bool `json:"disableAssistant,omitempty"`
}

// KagentiSpec opts a collective in to Kagenti's AgentCard auto-discovery
// (OpenSpec 20260527-agentcard-discovery, Phase 1).
//
// **Phase 1 is label-only.**  Discovery becomes functional once the A2A
// adapter serves /.well-known/agent-card.json (OpenSpec
// 20260527-a2a-agent-interop) and identity convergence (SPIRE x5c +
// Keycloak) binds the AgentCard's targetRef.  Until those land, leave
// this disabled — Kagenti will find the workload but fail to fetch a
// valid AgentCard.  See docs/kagenti-discovery.md.
type KagentiSpec struct {
	// Enabled is the master switch.  When false (default) the operator
	// stamps no Kagenti label.
	// +kubebuilder:default=false
	// +optional
	Enabled bool `json:"enabled,omitempty"`
}

// SpiffeSpec mirrors the SPIFFE-relevant subset of acc/config.py's
// SpiffeConfig.  Only the fields the operator reconciler needs to
// issue a ClusterSPIFFEID live here — the agent-side fields
// (svid_mount_path, jwt_audience, …) stay in acc-config.yaml.
//
// Proposal 012 PR-2 extends this struct with the edge-topology
// fields (edge_topology, parent_spire_url, …).
type SpiffeSpec struct {
	// Enabled is the master switch.  When false the operator issues
	// no ClusterSPIFFEID and the collective keeps the Ed25519 model.
	// +kubebuilder:default=false
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// TrustDomain is the SPIFFE trust domain, e.g. acc-prod.example.com.
	// Empty means the operator derives <corpus-name>.acc.local.
	// +optional
	TrustDomain string `json:"trustDomain,omitempty"`

	// EdgeTopology selects the edge SPIRE deployment topology
	// (proposal 012).  Only consulted when the owning AgentCorpus
	// has deployMode=edge.
	//   nested    — edge SPIRE server downstream of an rhoai parent;
	//               identities are site-qualified
	//               (spiffe://<td>/edge/<site>/role/<id>).
	//   federated — edge SPIRE owns its trust domain, federated with
	//               peers; no site-qualifier (the trust domain IS the
	//               scope).
	//   ed25519   — no edge SPIRE; legacy trust model.
	// +kubebuilder:validation:Enum=nested;federated;ed25519
	// +kubebuilder:default=nested
	// +optional
	EdgeTopology string `json:"edgeTopology,omitempty"`

	// EdgeSiteID qualifies the SPIFFE path so multiple edge sites
	// under a shared trust domain can never collide
	// (spiffe://<td>/edge/<site-id>/role/<id>).  Required when
	// deployMode=edge and edgeTopology=nested; ignored otherwise.
	// +optional
	EdgeSiteID string `json:"edgeSiteID,omitempty"`

	// FederationPeers lists SPIFFE bundle-endpoint URLs to federate
	// with (proposal 012 PR-3).  Required (>= 1 entry) when
	// edgeTopology=federated — each peer becomes a
	// ClusterFederatedTrustDomain custom resource so this edge's
	// SPIRE trusts SVIDs issued by the peer's trust domain.  Ignored
	// for nested / ed25519 topologies.
	// +optional
	FederationPeers []string `json:"federationPeers,omitempty"`
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
}

// AgentRoleSpec defines the deployment configuration for one agent role.
type AgentRoleSpec struct {
	// Role identifies the ACC agent role. Schema-level validation is the
	// regex pattern on the AgentRole type; semantic validation against the
	// operator's compiled-in catalogue happens in the AgentCollective webhook.
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
// +kubebuilder:validation:XValidation:rule="!self.deploy || (has(self.modelStoragePVC) && self.modelStoragePVC != '')",message="deploy=true requires modelStoragePVC (the PVC holding the model weights); without it the created InferenceService fails. On RHOAI prefer deploy=false + inferenceServiceRef to consume an existing model deployment."
type VLLMSpec struct {
	// InferenceServiceRef names the KServe InferenceService to consume.
	// The operator reads the model endpoint URL from the InferenceService
	// status and injects it into every agent pod as ACC_VLLM_INFERENCE_URL.
	// List candidates with: oc get inferenceservice -A
	InferenceServiceRef string `json:"inferenceServiceRef"`

	// InferenceServiceNamespace is the namespace of the referenced
	// InferenceService when it lives outside this corpus's namespace —
	// for example a model served from a different RHOAI Data Science
	// Project (workspace). Leave empty when the model runs in the same
	// namespace. Only meaningful with deploy=false. NOTE: cross-namespace
	// traffic may additionally require a NetworkPolicy (or service-mesh
	// membership) that lets this namespace reach the model's predictor
	// Service.
	// +optional
	InferenceServiceNamespace string `json:"inferenceServiceNamespace,omitempty"`

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
	// Validation: regex on the AgentRole type + webhook catalogue check.
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

	// SpiffeID is the workload identity the operator computed for this
	// collective, e.g. spiffe://acc-prod.example.com/role/research.
	// Empty when spiffe is disabled.  (Proposal 011 PR-2.)
	// +optional
	SpiffeID string `json:"spiffeID,omitempty"`

	// SpiffeIssued is true when a matching ClusterSPIFFEID custom
	// resource has been successfully created/updated.  False when
	// spiffe is disabled or spire-controller-manager is absent.
	// +optional
	SpiffeIssued bool `json:"spiffeIssued,omitempty"`

	// SpiffeError carries an operator-readable reason when SPIFFE
	// provisioning could not complete (e.g. SPIRE not installed).
	// Empty on success or when spiffe is disabled.
	// +optional
	SpiffeError string `json:"spiffeError,omitempty"`

	// EdgeSiteID echoes the site qualifier baked into SpiffeID when
	// the collective runs under deployMode=edge with edgeTopology=
	// nested (proposal 012 PR-2).  Empty for non-edge / non-nested.
	// +optional
	EdgeSiteID string `json:"edgeSiteID,omitempty"`
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
