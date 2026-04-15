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

// AgentCorpusSpec defines the desired state of an ACC deployment.
// +kubebuilder:object:generate=true
type AgentCorpusSpec struct {
	// DeployMode selects the infrastructure profile.
	// "standalone" provisions NATS + Redis + LanceDB (edge).
	// "rhoai" provisions NATS + Redis + Milvus and expects KServe / RHOAI operators.
	// +kubebuilder:validation:Enum=standalone;rhoai
	// +kubebuilder:default=standalone
	DeployMode DeployMode `json:"deployMode"`

	// Version pins the acc-agent-core image tag to deploy.
	// Must be a valid SemVer string.
	// +kubebuilder:validation:Pattern=`^v?[0-9]+\.[0-9]+\.[0-9]+.*$`
	// +kubebuilder:default="0.1.0"
	Version string `json:"version"`

	// ImageRegistry is the base registry for acc-agent-core and infrastructure images.
	// +kubebuilder:default="registry.access.redhat.com"
	// +optional
	ImageRegistry string `json:"imageRegistry,omitempty"`

	// Collectives lists references to AgentCollective resources this corpus manages.
	// Each collective gets its own set of agent Deployments and ScaledObjects.
	// +kubebuilder:validation:MinItems=1
	// +kubebuilder:validation:MaxItems=32
	Collectives []CollectiveRef `json:"collectives"`

	// Infrastructure configures the shared backing services.
	Infrastructure InfrastructureSpec `json:"infrastructure"`

	// Governance configures the 3-tier rule system.
	Governance GovernanceSpec `json:"governance"`

	// Kafka configures the optional NATS-to-Kafka audit bridge.
	// Kafka itself is NOT installed by the operator.
	// +optional
	Kafka *KafkaSpec `json:"kafka,omitempty"`

	// Observability configures OTel collector and Prometheus rules.
	// +optional
	Observability ObservabilitySpec `json:"observability,omitempty"`

	// UpgradePolicy controls how the operator handles ACC version upgrades.
	// +optional
	UpgradePolicy UpgradePolicySpec `json:"upgradePolicy,omitempty"`
}

// CollectiveRef references an AgentCollective resource in the same namespace.
type CollectiveRef struct {
	// Name is the metadata.name of the AgentCollective resource.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=63
	Name string `json:"name"`
}

// InfrastructureSpec defines all shared backing services.
type InfrastructureSpec struct {
	// NATS configures the JetStream StatefulSet.
	NATS NATSSpec `json:"nats"`

	// Redis configures the working memory store.
	Redis RedisSpec `json:"redis"`

	// Milvus configures the datacenter vector DB (rhoai mode only).
	// Required when deployMode=rhoai; ignored in standalone mode.
	// +optional
	Milvus *MilvusSpec `json:"milvus,omitempty"`
}

// NATSSpec configures the internal NATS JetStream deployment.
type NATSSpec struct {
	// Version is the NATS server image tag.
	// +kubebuilder:validation:Pattern=`^[0-9]+\.[0-9]+.*$`
	// +kubebuilder:default="2.10"
	Version string `json:"version"`

	// Replicas sets the NATS cluster size (1 = single-node, 3 = clustered).
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=5
	// +kubebuilder:default=1
	Replicas int32 `json:"replicas"`

	// StorageClass for the JetStream PVC.
	// +optional
	StorageClass string `json:"storageClass,omitempty"`

	// StorageSize for JetStream persistence.
	// +kubebuilder:default="2Gi"
	// +optional
	StorageSize string `json:"storageSize,omitempty"`

	// Resources sets CPU/memory for NATS pods.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
}

// RedisSpec configures the Redis working memory deployment.
type RedisSpec struct {
	// Version selects the UBI Redis image tag.
	// +kubebuilder:default="6"
	Version string `json:"version"`

	// Replicas (1 = standalone, 3 = Sentinel mode).
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=6
	// +kubebuilder:default=1
	Replicas int32 `json:"replicas"`

	// StorageSize for Redis persistence PVC.
	// +kubebuilder:default="1Gi"
	// +optional
	StorageSize string `json:"storageSize,omitempty"`

	// Resources sets CPU/memory for Redis pods.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
}

// MilvusSpec configures Milvus connectivity (RHOAI mode).
// The operator does NOT install Milvus; it probes connectivity and creates the
// Milvus credentials Secret.
type MilvusSpec struct {
	// URI is the Milvus gRPC endpoint.
	// +kubebuilder:validation:MinLength=1
	URI string `json:"uri"`

	// CollectionPrefix is prepended to all ACC Milvus collection names.
	// +kubebuilder:default="acc_"
	// +optional
	CollectionPrefix string `json:"collectionPrefix,omitempty"`

	// CredentialsSecretRef references a Secret with milvus_user / milvus_password.
	// +optional
	CredentialsSecretRef *corev1.SecretReference `json:"credentialsSecretRef,omitempty"`
}

// GovernanceSpec configures the OPA 3-tier rule system.
type GovernanceSpec struct {
	// CategoryA configures immutable constitutional rule enforcement.
	CategoryA CategoryASpec `json:"categoryA"`

	// CategoryB configures the live-updatable OPA bundle server.
	CategoryB CategoryBSpec `json:"categoryB"`

	// CategoryC configures adaptive rule generation by the arbiter.
	// +optional
	CategoryC *CategoryCSpec `json:"categoryC,omitempty"`

	// GatekeeperIntegration enables syncing Category A rules as OPA Gatekeeper
	// ConstraintTemplates (requires Gatekeeper to be installed cluster-wide).
	// +kubebuilder:default=false
	GatekeeperIntegration bool `json:"gatekeeperIntegration"`
}

// CategoryASpec configures Category A (immutable) rule enforcement.
type CategoryASpec struct {
	// WASMConfigMapRef names the ConfigMap holding the compiled
	// category_a.wasm blob to mount into each agent pod.
	// +kubebuilder:validation:MinLength=1
	WASMConfigMapRef string `json:"wasmConfigMapRef"`
}

// CategoryBSpec configures the OPA bundle server for live-updatable rules.
type CategoryBSpec struct {
	// BundleServerImage is the OPA bundle server image.
	// +kubebuilder:default="openpolicyagent/opa:latest"
	// +optional
	BundleServerImage string `json:"bundleServerImage,omitempty"`

	// BundlePVCSize is the PVC size for the bundle store.
	// +kubebuilder:default="500Mi"
	// +optional
	BundlePVCSize string `json:"bundlePVCSize,omitempty"`

	// PollIntervalSeconds controls how often agent OPA sidecars poll the bundle server.
	// +kubebuilder:validation:Minimum=10
	// +kubebuilder:validation:Maximum=300
	// +kubebuilder:default=30
	// +optional
	PollIntervalSeconds int32 `json:"pollIntervalSeconds,omitempty"`
}

// CategoryCSpec configures arbiter-signed adaptive rule generation.
type CategoryCSpec struct {
	// ConfidenceThreshold is the minimum ICL confidence an arbiter must have
	// before signing a Category C rule. Expressed as a decimal string, e.g. "0.80".
	// +kubebuilder:validation:Pattern=`^0\.[0-9]+$|^1\.0+$`
	// +optional
	ConfidenceThreshold string `json:"confidenceThreshold,omitempty"`

	// MaxRulesPerCollective caps the number of active Cat-C rules.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=1000
	// +kubebuilder:default=100
	// +optional
	MaxRulesPerCollective int32 `json:"maxRulesPerCollective,omitempty"`
}

// KafkaSpec configures the NATS-to-Kafka bridge.
// Kafka is a cluster prerequisite — NOT installed by the operator.
type KafkaSpec struct {
	// BootstrapServers is a comma-separated list of Kafka broker addresses.
	// The operator TCP-probes these to verify connectivity.
	// +kubebuilder:validation:MinLength=1
	BootstrapServers string `json:"bootstrapServers"`

	// AuditTopic is the Kafka topic for all signal audit records.
	// +kubebuilder:default="acc.audit.all"
	// +optional
	AuditTopic string `json:"auditTopic,omitempty"`

	// SignalTopicsPrefix is the prefix for per-signal-type Kafka topics.
	// +kubebuilder:default="acc.signals"
	// +optional
	SignalTopicsPrefix string `json:"signalTopicsPrefix,omitempty"`

	// BridgeReplicas sets the number of bridge Deployment replicas.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=10
	// +kubebuilder:default=1
	// +optional
	BridgeReplicas int32 `json:"bridgeReplicas,omitempty"`

	// CredentialsSecretRef references a Secret with kafka_sasl_username /
	// kafka_sasl_password keys.
	// +optional
	CredentialsSecretRef *corev1.SecretReference `json:"credentialsSecretRef,omitempty"`
}

// ObservabilitySpec configures telemetry collection.
type ObservabilitySpec struct {
	// Backend selects the telemetry backend.
	// +kubebuilder:validation:Enum=log;otel
	// +kubebuilder:default=log
	Backend MetricsBackend `json:"backend"`

	// OTelCollector configures the OpenTelemetry collector deployment.
	// Required when backend=otel.
	// +optional
	OTelCollector *OTelCollectorSpec `json:"otelCollector,omitempty"`

	// PrometheusRules enables creation of PrometheusRule CRs for ACC alerts.
	// +kubebuilder:default=true
	// +optional
	PrometheusRules bool `json:"prometheusRules,omitempty"`

	// GrafanaDashboard enables creation of a ConfigMap-based Grafana dashboard.
	// +kubebuilder:default=false
	// +optional
	GrafanaDashboard bool `json:"grafanaDashboard,omitempty"`
}

// OTelCollectorSpec configures the OTel collector deployment.
type OTelCollectorSpec struct {
	// Endpoint is the OTLP gRPC/HTTP endpoint to export telemetry to.
	// +kubebuilder:validation:MinLength=1
	Endpoint string `json:"endpoint"`

	// ServiceName is the OTel service.name resource attribute.
	// +kubebuilder:default="acc-agent"
	// +optional
	ServiceName string `json:"serviceName,omitempty"`

	// TLSInsecure disables TLS verification for the remote OTLP endpoint.
	// Use only in development environments.
	// +kubebuilder:default=false
	// +optional
	TLSInsecure bool `json:"tlsInsecure,omitempty"`
}

// UpgradePolicySpec controls how the operator handles version upgrades.
type UpgradePolicySpec struct {
	// Mode selects the upgrade strategy.
	// +kubebuilder:validation:Enum=auto;manual
	// +kubebuilder:default=auto
	Mode UpgradeMode `json:"mode"`

	// RequireApproval forces the operator to pause before upgrading shared
	// infrastructure components (NATS, Redis). When true, the operator writes
	// a Warning Event and sets UpgradeApprovalPending=True, then waits for the
	// annotation acc.redhat.io/approve-upgrade=<version> to be applied.
	// +kubebuilder:default=false
	// +optional
	RequireApproval bool `json:"requireApproval,omitempty"`

	// MinKafkaVersion is the minimum Kafka broker version required for bridge
	// compatibility. Operator warns but does not block if Kafka is older.
	// +optional
	MinKafkaVersion string `json:"minKafkaVersion,omitempty"`
}

// -----------------------------------------------------------------------
// Status
// -----------------------------------------------------------------------

// AgentCorpusStatus reports the observed state of the full ACC deployment.
type AgentCorpusStatus struct {
	// Phase is the top-level operational state.
	// +optional
	Phase CorpusPhase `json:"phase,omitempty"`

	// ObservedGeneration is the .metadata.generation this status reflects.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions contains the standard metav1.Condition set.
	// Known types: Ready, InfrastructureReady, CollectivesReady,
	// PrerequisitesMet, KafkaBridgeReady, UpgradeApprovalPending.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// Prerequisites reports the detected presence of each optional cluster dependency.
	// Written by the PrerequisiteReconciler; read by downstream sub-reconcilers.
	// +optional
	Prerequisites PrerequisiteStatus `json:"prerequisites,omitempty"`

	// Infrastructure reports the state of operator-managed shared components.
	// +optional
	Infrastructure InfrastructureStatus `json:"infrastructure,omitempty"`

	// CollectiveStatuses maps collective name → its operational status.
	// +optional
	CollectiveStatuses map[string]CollectiveStatus `json:"collectiveStatuses,omitempty"`

	// KafkaBridgeReady is true when the Kafka bridge Deployment is available.
	// +optional
	KafkaBridgeReady bool `json:"kafkaBridgeReady,omitempty"`

	// CurrentVersion is the ACC version currently deployed.
	// +optional
	CurrentVersion string `json:"currentVersion,omitempty"`

	// PendingUpgradeVersion is set when upgradePolicy.requireApproval=true and a
	// version change is pending user approval.
	// +optional
	PendingUpgradeVersion string `json:"pendingUpgradeVersion,omitempty"`
}

// PrerequisiteStatus holds flat boolean flags for each optional cluster dependency.
// These are written by PrerequisiteReconciler and read by downstream sub-reconcilers
// within the same reconcile pass.
type PrerequisiteStatus struct {
	// KEDAInstalled is true when the KEDA API group (keda.sh) is present.
	KEDAInstalled bool `json:"kedaInstalled,omitempty"`

	// GatekeeperInstalled is true when templates.gatekeeper.sh is registered.
	GatekeeperInstalled bool `json:"gatekeeperInstalled,omitempty"`

	// RHOAIInstalled is true when the ODH/RHOAI API group is registered.
	RHOAIInstalled bool `json:"rhoaiInstalled,omitempty"`

	// KServeInstalled is true when serving.kserve.io is registered.
	KServeInstalled bool `json:"kserveInstalled,omitempty"`

	// PrometheusRulesSupported is true when monitoring.coreos.com is registered.
	PrometheusRulesSupported bool `json:"prometheusRulesSupported,omitempty"`

	// KafkaReachable is true when the configured Kafka bootstrap servers are TCP-reachable.
	KafkaReachable bool `json:"kafkaReachable,omitempty"`

	// AllMet is true when every required prerequisite is satisfied.
	AllMet bool `json:"allMet,omitempty"`
}

// InfrastructureStatus reports the state of operator-managed components.
type InfrastructureStatus struct {
	NATSReady          bool   `json:"natsReady,omitempty"`
	NATSVersion        string `json:"natsVersion,omitempty"`
	RedisReady         bool   `json:"redisReady,omitempty"`
	RedisVersion       string `json:"redisVersion,omitempty"`
	MilvusConnected    bool   `json:"milvusConnected,omitempty"`
	OPABundleReady     bool   `json:"opaBundleReady,omitempty"`
	OTelCollectorReady bool   `json:"otelCollectorReady,omitempty"`
}

// CollectiveStatus reports per-collective operational state (embedded in AgentCorpus status).
type CollectiveStatus struct {
	Phase               CollectivePhase    `json:"phase,omitempty"`
	ReadyAgents         map[string]int32   `json:"readyAgents,omitempty"`
	DesiredAgents       map[string]int32   `json:"desiredAgents,omitempty"`
	ScaledObjectsActive bool               `json:"scaledObjectsActive,omitempty"`
	KServeReady         bool               `json:"kserveReady,omitempty"`
	Conditions          []metav1.Condition `json:"conditions,omitempty"`
}

// -----------------------------------------------------------------------
// AgentCorpus resource
// -----------------------------------------------------------------------

// AgentCorpus is the primary resource representing a full ACC deployment.
//
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced,shortName=ac,categories=acc
// +kubebuilder:printcolumn:name="Mode",type="string",JSONPath=".spec.deployMode"
// +kubebuilder:printcolumn:name="Version",type="string",JSONPath=".spec.version"
// +kubebuilder:printcolumn:name="Phase",type="string",JSONPath=".status.phase"
// +kubebuilder:printcolumn:name="Age",type="date",JSONPath=".metadata.creationTimestamp"
type AgentCorpus struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   AgentCorpusSpec   `json:"spec,omitempty"`
	Status AgentCorpusStatus `json:"status,omitempty"`
}

// AgentCorpusList contains a list of AgentCorpus.
// +kubebuilder:object:root=true
type AgentCorpusList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []AgentCorpus `json:"items"`
}

func init() {
	SchemeBuilder.Register(&AgentCorpus{}, &AgentCorpusList{})
}
