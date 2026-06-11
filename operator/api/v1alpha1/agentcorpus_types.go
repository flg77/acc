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
	// "standalone" — local dev / CI / Podman Compose.
	// "edge" — MicroShift / K3s / production edge node; NATS runs as a leaf node
	//           connecting to a datacenter hub when network is available.
	// "rhoai" — OpenShift datacenter with RHOAI GPU inference.
	//
	// PREREQUISITES: "rhoai" requires RHOAI / OpenShift AI installed (a
	// DataScienceCluster) and a Milvus URI (infrastructure.milvus.uri). On a
	// fresh AgentCorpus this field is auto-defaulted to "rhoai" when the operator
	// detects RHOAI on the cluster, else "standalone".
	// NOTE: NATS JetStream + Redis are always provisioned by the operator. In a
	// shared RHOAI datacenter, confirm JetStream storage + NetworkPolicy fit your
	// cluster — for large agent fleets prefer the Kafka audit bridge (see .kafka).
	// No static default: the mutating webhook auto-detects RHOAI and sets
	// "rhoai" (else "standalone"). +optional so an unset value reaches the
	// defaulter instead of being rejected as required.
	// +kubebuilder:validation:Enum=standalone;rhoai;edge
	// +optional
	DeployMode DeployMode `json:"deployMode,omitempty"`

	// Version pins the acc-agent-core image tag to deploy.
	// Must be a valid SemVer string.
	// +kubebuilder:validation:Pattern=`^v?[0-9]+\.[0-9]+\.[0-9]+.*$`
	// +kubebuilder:default="0.1.0"
	Version string `json:"version"`

	// ImageRegistry is the base registry for acc-agent-core and infrastructure images.
	// Used when ImageRepository is empty: images render as
	// <imageRegistry>/<component>:<tag>.
	// +kubebuilder:default="registry.access.redhat.com"
	// +optional
	ImageRegistry string `json:"imageRegistry,omitempty"`

	// ImageRepository, when set, addresses every component within a single
	// container repository, distinguished by tag: images render as
	// <imageRepository>:<component>-<tag> (e.g.
	// quay.io/flg77/acc_images:acc-agent-core-0.1.0). Use this for registries
	// that can only host one repository. When empty, ImageRegistry is used.
	// +optional
	ImageRepository string `json:"imageRepository,omitempty"`

	// ImagePullSecrets is an optional list of Secret names used to pull the
	// component images. When set, the operator adds them to the imagePullSecrets
	// of every pod it renders (agents, NATS, Redis, bridges). Required when the
	// target registry/repository is private.
	// +optional
	ImagePullSecrets []string `json:"imagePullSecrets,omitempty"`

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
	// RECOMMENDED FOR LARGE AGENT FLEETS: at scale, Kafka is the preferred audit
	// transport (durable, high-throughput) over NATS-only. Kafka itself is NOT
	// installed by the operator — install AMQ Streams / Strimzi first (OpenShift:
	// the "AMQ Streams" / "Streams for Apache Kafka" Operator from OperatorHub),
	// then set bootstrapServers here. See docs/observability + the operator
	// description for the prerequisite link.
	// +optional
	Kafka *KafkaSpec `json:"kafka,omitempty"`

	// Observability configures OTel collector and Prometheus rules.
	// +optional
	Observability ObservabilitySpec `json:"observability,omitempty"`

	// UpgradePolicy controls how the operator handles ACC version upgrades.
	// +optional
	UpgradePolicy UpgradePolicySpec `json:"upgradePolicy,omitempty"`

	// Edge configures edge-specific behaviour (deployMode=edge only).
	// Ignored when deployMode is standalone or rhoai.
	// +optional
	Edge *EdgeSpec `json:"edge,omitempty"`

	// NetworkPolicy configures the optional capability-tiered network
	// isolation layer for agent and infrastructure pods (proposal 014,
	// security roadmap Phase 1).  When nil or disabled the operator
	// emits no policy objects — unchanged legacy behaviour.
	// +optional
	NetworkPolicy *NetworkPolicySpec `json:"networkPolicy,omitempty"`

	// MCPServers configures shared MCP servers visible to every collective in
	// this corpus. Each entry produces a Deployment + Service named
	// acc-mcp-{name}, matching the URL convention used by mcps/<name>/mcp.yaml.
	// The reconciler that owns these objects ships in PR-51 (this PR only adds
	// the schema).
	// +optional
	// +kubebuilder:validation:MaxItems=16
	MCPServers []MCPServerSpec `json:"mcpServers,omitempty"`

	// ManifestDelivery controls how the operator-baked roles/, skills/, and
	// mcps/ trees reach agent pods.
	//   "all"  (default) — operator emits ConfigMaps and mounts them at
	//                      /etc/acc/{roles,skills,mcps} in every agent pod.
	//   "none" — operator skips the mounts; users must bake the trees into
	//            a custom agent image.
	// The reconciler that emits the ConfigMaps and the volume injection in
	// agent pods both ship in PR-50 (this PR only adds the schema).
	// +kubebuilder:validation:Enum=all;none
	// +kubebuilder:default=all
	// +optional
	ManifestDelivery string `json:"manifestDelivery,omitempty"`
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

	// Image, when set, is the FULL NATS container image reference and
	// overrides the registry/repository-derived default from
	// util.ComponentImage (i.e. "<imageRepository>:nats-<version>-alpine" or
	// "<imageRegistry>/nats:<version>-alpine"). Use this when neither the
	// single ImageRepository nor ImageRegistry hosts a usable nats image — a
	// fresh RHOAI cluster with imageRegistry=registry.access.redhat.com hits
	// ImagePullBackOff "name unknown: Repo not found" (observed on the c26sx
	// sandbox 2026-06-10). Set e.g. docker.io/library/nats:2.10-alpine.
	// +optional
	Image string `json:"image,omitempty"`

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

	// NKeyAuth enables per-role NATS NKey authentication (proposal
	// 013, Phase 0c).  When nil or disabled the NATS server runs
	// without an authorization block — unchanged legacy behaviour.
	// +optional
	NKeyAuth *NKeyAuthSpec `json:"nkeyAuth,omitempty"`
}

// NKeyAuthSpec configures NATS NKey authentication (proposal 013).
type NKeyAuthSpec struct {
	// Enabled turns on per-role NKey authentication: the operator
	// generates an eight-identity NKey Secret, renders an
	// authorization block into nats.conf, and projects each agent's
	// role seed into its pod.
	// +kubebuilder:default=false
	// +optional
	Enabled bool `json:"enabled,omitempty"`
}

// RedisSpec configures the Redis working memory deployment.
type RedisSpec struct {
	// Version selects the UBI Redis image tag.
	// +kubebuilder:default="6"
	Version string `json:"version"`

	// Image, when set, is the FULL Redis container image reference and
	// overrides the util.ComponentImage-derived default. Same rationale as
	// NATSSpec.Image — set e.g. docker.io/library/redis:7-alpine when neither
	// imageRepository nor imageRegistry hosts a usable redis image.
	// +optional
	Image string `json:"image,omitempty"`

	// Replicas (1 = standalone, 3 = Sentinel mode).
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=6
	// +kubebuilder:default=1
	Replicas int32 `json:"replicas"`

	// StorageClass for the Redis persistence PVC. When empty, storageClassName
	// is left unset so the cluster's default StorageClass applies.
	// +optional
	StorageClass string `json:"storageClass,omitempty"`

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

// EdgeSpec configures edge-specific behaviour (deployMode=edge only).
type EdgeSpec struct {
	// HubNatsUrl is the NATS leaf node remote URL of the datacenter hub.
	// When set, the operator renders a leafnodes block in nats.conf so the
	// local NATS server connects to the hub and forwards bridge subjects.
	// Format: nats-leaf://hub.example.com:7422
	// +optional
	HubNatsUrl string `json:"hubNatsUrl,omitempty"`

	// HubCollectiveID is the collective ID of the datacenter hub that edge
	// agents should delegate complex tasks to (ACC-9 bridge protocol).
	// Rendered as ACC_HUB_COLLECTIVE_ID in agent pod environments.
	// +optional
	HubCollectiveID string `json:"hubCollectiveId,omitempty"`

	// HubRegistry is the container image registry to pull from when the
	// edge node has connectivity to the hub (image pull on reconnect).
	// Defaults to spec.imageRegistry when empty.
	// +optional
	HubRegistry string `json:"hubRegistry,omitempty"`

	// RedisMaxMemoryMB caps Redis working memory (MiB) to prevent OOM on
	// edge hardware. When > 0, sets Redis maxmemory and maxmemory-policy.
	// +kubebuilder:validation:Minimum=64
	// +kubebuilder:default=512
	// +optional
	RedisMaxMemoryMB int32 `json:"redisMaxMemoryMb,omitempty"`

	// RedisMaxMemoryPolicy controls Redis eviction when maxmemory is reached.
	// +kubebuilder:validation:Enum=allkeys-lru;allkeys-lfu;volatile-lru;noeviction
	// +kubebuilder:default=allkeys-lru
	// +optional
	RedisMaxMemoryPolicy string `json:"redisMaxMemoryPolicy,omitempty"`
}

// NetworkPolicySpec configures the capability-tiered network isolation
// layer (proposal 014, security roadmap Phase 1).
//
// The operator emits the highest-tier policy the running cluster's CNI
// can enforce, capped by MaxTier.  Tier 1 = standard Kubernetes
// NetworkPolicy (L3/L4); Tier 2 = FQDN egress via OVN EgressFirewall or
// Cilium CiliumNetworkPolicy; Tier 3 = full L7 (Cilium only).  When
// Enabled is false (the default) no policy objects are emitted.
type NetworkPolicySpec struct {
	// Enabled is the master switch.  Default false — the operator
	// emits no NetworkPolicy/EgressFirewall/CiliumNetworkPolicy objects
	// and the collective behaves exactly as before.
	// +kubebuilder:default=false
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// MaxTier is the highest policy tier the operator is allowed to
	// emit.  The active tier is min(MaxTier, clusterCapability).
	// 1 = L4 NetworkPolicy; 2 = FQDN egress; 3 = L7 (Cilium).
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=3
	// +kubebuilder:default=1
	// +optional
	MaxTier int32 `json:"maxTier,omitempty"`

	// Mode selects enforcement behaviour.  "enforce" drops disallowed
	// traffic; "audit" logs it without dropping (a safe canary path —
	// honoured only at Tier 3 with Cilium policy-audit).
	// +kubebuilder:validation:Enum=enforce;audit
	// +kubebuilder:default=enforce
	// +optional
	Mode string `json:"mode,omitempty"`

	// CNIEnforces overrides the heuristic that detects whether the
	// running CNI enforces NetworkPolicy.  "auto" (default) infers it;
	// "true"/"false" force the verdict (use on K3s where the heuristic
	// cannot be certain).
	// +kubebuilder:validation:Enum=auto;"true";"false"
	// +kubebuilder:default=auto
	// +optional
	CNIEnforces string `json:"cniEnforces,omitempty"`

	// ExtraEgressFQDNs are additional fully-qualified domain names the
	// agent pods are allowed to reach (Tier 2+).  E.g. a custom LLM
	// provider or a private Slack proxy.
	// +optional
	ExtraEgressFQDNs []string `json:"extraEgressFQDNs,omitempty"`

	// ExtraEgressCIDRs are additional CIDR blocks agent pods may reach
	// — the escape hatch for non-DNS external endpoints (e.g. a
	// fixed-IP NATS hub).
	// +optional
	ExtraEgressCIDRs []string `json:"extraEgressCIDRs,omitempty"`

	// AllowedExternalLLM overrides the built-in set of external LLM
	// provider FQDNs used to scope Tier 2 egress.  When empty a
	// sensible default set (Anthropic + common OpenAI-compatible
	// hosts) is used.
	// +optional
	AllowedExternalLLM []string `json:"allowedExternalLLM,omitempty"`
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
	// ConstraintTemplates for admission-time enforcement.
	// PREREQUISITE (manual): install OPA Gatekeeper first — the operator does NOT
	// install it, it only syncs rules when present. On OpenShift install the
	// "Gatekeeper Operator" from OperatorHub (or upstream gatekeeper). Leave
	// false until Gatekeeper is available, else the synced ConstraintTemplates
	// have no controller to honour them.
	// +kubebuilder:default=false
	GatekeeperIntegration bool `json:"gatekeeperIntegration"`

	// RuntimeEvidence configures the optional provider-agnostic
	// kernel-event evidence layer for Category-A governance (proposal
	// 015, security roadmap Phase 3).  When nil or disabled the
	// operator emits nothing and Cat-A stays metadata-only.
	// +optional
	RuntimeEvidence *RuntimeEvidenceSpec `json:"runtimeEvidence,omitempty"`
}

// RuntimeEvidenceSpec configures the kernel-event evidence layer
// (proposal 015).  ACC is a *consumer* of runtime-security evidence: it
// detects whichever backend the cluster runs (RHACS / Falco / Tetragon
// for process+file events, NetObserv for network-connect events),
// normalises events onto the NATS bus as KERNEL_EVENT signals, and
// folds them into Category-A evaluation.  It never installs or operates
// a runtime-security tool.
type RuntimeEvidenceSpec struct {
	// Enabled is the master switch.  Default false — the operator
	// emits no bridge and Cat-A behaves exactly as before.
	// +kubebuilder:default=false
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Enforce selects enforcement behaviour.  False (default) = a
	// 4-week observe baseline: kernel violations are logged
	// (`OBSERVED:kernel:*`) but never block.  True = violations block
	// and emit ALERT_ESCALATE.
	// +kubebuilder:default=false
	// +optional
	Enforce bool `json:"enforce,omitempty"`

	// ObserveWindowDays is the recommended observe baseline length
	// before flipping Enforce on.  Advisory — surfaced in status.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:default=28
	// +optional
	ObserveWindowDays int32 `json:"observeWindowDays,omitempty"`

	// PreferredBackend picks the process/file evidence backend when
	// more than one is detected.  "auto" (default) prefers
	// RHACS > Falco > Tetragon.
	// +kubebuilder:validation:Enum=auto;rhacs;falco;tetragon
	// +kubebuilder:default=auto
	// +optional
	PreferredBackend string `json:"preferredBackend,omitempty"`
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

// MCPServerSpec configures one MCP server Deployment + Service.
// The operator emits a Deployment named acc-mcp-{Name} and a Service of the
// same name on Port. The Service name matches the url: field convention used
// by mcps/<Name>/mcp.yaml so agents resolve the server without manifest edits.
//
// The reconciler that owns these objects ships in PR-51 of the
// 20260508-operator-feature-parity-d-e openspec change.
type MCPServerSpec struct {
	// Name matches the directory name under mcps/ in the source tree
	// (e.g. "web-search-brave"). DNS-label-safe.
	// +kubebuilder:validation:Pattern=`^[a-z][a-z0-9-]{1,62}$`
	// +kubebuilder:validation:MinLength=2
	// +kubebuilder:validation:MaxLength=63
	Name string `json:"name"`

	// Image is the full container image reference for the MCP server,
	// including registry, repository, and tag.
	// +kubebuilder:validation:MinLength=1
	Image string `json:"image"`

	// Replicas sets the Deployment replica count.
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=10
	// +kubebuilder:default=1
	// +optional
	Replicas int32 `json:"replicas,omitempty"`

	// Port is the JSON-RPC port the server listens on. Becomes the Service
	// port and the targetPort. Convention across the bundled MCPs is 8080.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=65535
	// +kubebuilder:default=8080
	// +optional
	Port int32 `json:"port,omitempty"`

	// Env injects environment variables into the MCP container.
	// +optional
	Env []corev1.EnvVar `json:"env,omitempty"`

	// SecretEnv pulls env vars from referenced Secrets / ConfigMaps via
	// envFrom (typical use: BRAVE_API_KEY for web-search-brave, an Anthropic
	// or OpenAI key for web-browser-harness).
	// +optional
	SecretEnv []corev1.EnvFromSource `json:"secretEnv,omitempty"`

	// ShmSizeMi mounts a Memory-medium emptyDir at /dev/shm sized to the
	// requested mebibytes. Required for the browser-harness MCP because
	// Chromium crashes intermittently with the default 64 MiB tmpfs;
	// 256 MiB is browser-use's documented minimum.
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=4096
	// +kubebuilder:default=0
	// +optional
	ShmSizeMi int32 `json:"shmSizeMi,omitempty"`

	// Resources sets CPU/memory requests and limits for the MCP container.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
}

// MCPServerStatus reports the operational state of one MCP server.
// Aggregated into AgentCorpusStatus.MCPServerStatuses by the MCP reconciler.
type MCPServerStatus struct {
	// Ready is true when the Deployment's ReadyReplicas matches Replicas.
	Ready bool `json:"ready"`

	// Replicas is the live ReadyReplicas count from the Deployment.
	Replicas int32 `json:"replicas"`

	// ServiceURL is the in-cluster JSON-RPC URL agents should call.
	// Format: http://acc-mcp-{name}.{namespace}.svc.cluster.local:{port}/rpc
	ServiceURL string `json:"serviceURL,omitempty"`
}

// ObservabilitySpec configures telemetry collection.
type ObservabilitySpec struct {
	// Backend selects the telemetry backend. Defaults to "otel": the operator
	// deploys an OpenTelemetry Collector and the webhook points agents at it, so
	// traces/metrics flow out of the box. Set "log" for the minimal (stdout)
	// profile.
	// +kubebuilder:validation:Enum=log;otel
	// +kubebuilder:default=otel
	Backend MetricsBackend `json:"backend"`

	// OTelCollector configures the OpenTelemetry collector deployment.
	// Required when backend=otel.
	// +optional
	OTelCollector *OTelCollectorSpec `json:"otelCollector,omitempty"`

	// PrometheusRules enables creation of PrometheusRule CRs for ACC alerts.
	// Not "omitempty": the field must serialize even when false, otherwise the
	// defaulting webhook's re-marshal drops it and the +kubebuilder:default=true
	// re-applies, making it impossible to disable via the CR.
	// +kubebuilder:default=true
	// +optional
	PrometheusRules bool `json:"prometheusRules"`

	// GrafanaDashboard enables creation of a ConfigMap-based Grafana dashboard.
	// Defaults to true so a dashboard ships out of the box (harmless if no
	// Grafana operator is present — it is just a ConfigMap).
	// +kubebuilder:default=true
	// +optional
	GrafanaDashboard bool `json:"grafanaDashboard,omitempty"`
}

// OTelCollectorSpec configures the OTel collector deployment.
type OTelCollectorSpec struct {
	// Endpoint is the OTLP gRPC/HTTP endpoint to export telemetry to.
	// +kubebuilder:validation:MinLength=1
	Endpoint string `json:"endpoint"`

	// Protocol selects the OTLP transport agents use to reach Endpoint.
	// Matches the upstream OTel spec env var OTEL_EXPORTER_OTLP_PROTOCOL.
	// Default "grpc" preserves pre-Phase-3 behaviour (port :4317);
	// "http/protobuf" targets HTTP collectors / MLflow /v1/traces on
	// :4318 — see docs/observability/mlflow.md.
	// +kubebuilder:validation:Enum=grpc;http/protobuf
	// +kubebuilder:default=grpc
	// +optional
	Protocol string `json:"protocol,omitempty"`

	// ServiceName is the OTel service.name resource attribute.
	// +kubebuilder:default="acc-agent"
	// +optional
	ServiceName string `json:"serviceName,omitempty"`

	// TLSInsecure disables TLS verification for the remote OTLP endpoint.
	// Use only in development environments.
	// +kubebuilder:default=false
	// +optional
	TLSInsecure bool `json:"tlsInsecure,omitempty"`

	// MLflowEndpoint is an optional additional OTLP/HTTP endpoint to
	// fan traces out to alongside the primary Endpoint.  When set, the
	// operator-rendered Collector config gains an extra otlphttp/mlflow
	// exporter on the traces pipeline.  Leave empty to skip the fan-out
	// (operators who run a standalone Collector configure MLflow there).
	// +optional
	MLflowEndpoint string `json:"mlflowEndpoint,omitempty"`
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

	// MCPServerStatuses reports the per-MCP-server state. Keyed by
	// MCPServerSpec.Name. Populated by the MCP reconciler that ships in PR-51.
	// +optional
	MCPServerStatuses map[string]MCPServerStatus `json:"mcpServerStatuses,omitempty"`

	// ManifestDeliveryReady is true when the acc-roles, acc-skills, and
	// acc-mcps ConfigMaps have been emitted by the manifest delivery
	// reconciler that ships in PR-50.
	// +optional
	ManifestDeliveryReady bool `json:"manifestDeliveryReady,omitempty"`

	// NetworkPolicy reports what the NetworkPolicyReconciler emitted
	// (proposal 014).  The companion NetworkPolicyReady condition
	// carries the human-readable reason.
	// +optional
	NetworkPolicy NetworkPolicyStatus `json:"networkPolicy,omitempty"`

	// RuntimeEvidence reports the kernel-event evidence layer state
	// (proposal 015).  The companion RuntimeEvidenceReady condition
	// carries the human-readable reason.
	// +optional
	RuntimeEvidence RuntimeEvidenceStatus `json:"runtimeEvidence,omitempty"`

	// AvailableRHOAIModels lists READY RHOAI / KServe InferenceServices
	// discovered on the cluster (name, namespace, in-cluster URL). Surfaced so an
	// operator can wire an in-cluster model as an AgentCollective LLM backend
	// instead of an external provider. Populated only when deployMode=rhoai.
	// +optional
	AvailableRHOAIModels []RHOAIModelRef `json:"availableRHOAIModels,omitempty"`

	// CurrentVersion is the ACC version currently deployed.
	// +optional
	CurrentVersion string `json:"currentVersion,omitempty"`

	// PendingUpgradeVersion is set when upgradePolicy.requireApproval=true and a
	// version change is pending user approval.
	// +optional
	PendingUpgradeVersion string `json:"pendingUpgradeVersion,omitempty"`
}

// RHOAIModelRef identifies a READY RHOAI / KServe InferenceService discovered
// on the cluster.
type RHOAIModelRef struct {
	// Name is the InferenceService name.
	Name string `json:"name"`
	// Namespace is the InferenceService namespace.
	Namespace string `json:"namespace"`
	// URL is the in-cluster predictor URL (.status.url of the InferenceService).
	// +optional
	URL string `json:"url,omitempty"`
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

	// SpireInstalled is true when the spire.spiffe.io API group is
	// registered (spire-controller-manager present).  Gates SPIFFE
	// workload-identity provisioning — proposal 011.
	SpireInstalled bool `json:"spireInstalled,omitempty"`

	// CiliumInstalled is true when the cilium.io API group is
	// registered.  Enables Tier 2/3 network policy — proposal 014.
	CiliumInstalled bool `json:"ciliumInstalled,omitempty"`

	// OVNEgressFirewallSupported is true when the k8s.ovn.org API
	// group is registered (OVN-Kubernetes CNI).  Enables Tier 2 FQDN
	// egress via EgressFirewall — proposal 014.
	OVNEgressFirewallSupported bool `json:"ovnEgressFirewallSupported,omitempty"`

	// RHACSInstalled is true when the platform.stackrox.io API group
	// is registered (Red Hat Advanced Cluster Security) — a
	// runtime-evidence backend, proposal 015.
	RHACSInstalled bool `json:"rhacsInstalled,omitempty"`

	// FalcoInstalled is true when the Falco CRD group is registered —
	// a runtime-evidence backend, proposal 015.
	FalcoInstalled bool `json:"falcoInstalled,omitempty"`

	// TetragonInstalled is true when the TracingPolicy resource (kind,
	// under cilium.io/v1alpha1) is registered — a runtime-evidence
	// backend, proposal 015.  Detected resource-level, NOT by the
	// cilium.io group (that would collide with CiliumInstalled).
	TetragonInstalled bool `json:"tetragonInstalled,omitempty"`

	// NetObservInstalled is true when the flows.netobserv.io API group
	// is registered (OpenShift Network Observability) — the
	// network-connect evidence source, proposal 015.
	NetObservInstalled bool `json:"netobservInstalled,omitempty"`

	// AllMet is true when every required prerequisite is satisfied.
	AllMet bool `json:"allMet,omitempty"`
}

// NetworkPolicyStatus reports what the NetworkPolicyReconciler emitted
// (proposal 014).  Written each reconcile pass.
type NetworkPolicyStatus struct {
	// ActiveTier is the policy tier actually emitted —
	// min(spec.networkPolicy.maxTier, clusterCapability).  0 means no
	// policy (standalone, disabled, or a non-enforcing CNI).
	// +optional
	ActiveTier int32 `json:"activeTier,omitempty"`

	// Backend names the policy engine in use: "none", "networkpolicy",
	// "egressfirewall", or "cilium".
	// +optional
	Backend string `json:"backend,omitempty"`

	// PolicyCount is the number of policy objects the operator owns for
	// this corpus.
	// +optional
	PolicyCount int32 `json:"policyCount,omitempty"`
}

// RuntimeEvidenceStatus reports the kernel-event evidence layer state
// (proposal 015).  Written each reconcile pass.
type RuntimeEvidenceStatus struct {
	// ActiveBackend names the selected process/file evidence backend:
	// "none", "rhacs", "falco", or "tetragon".
	// +optional
	ActiveBackend string `json:"activeBackend,omitempty"`

	// NetworkSource names the network-connect evidence source:
	// "none" or "netobserv".
	// +optional
	NetworkSource string `json:"networkSource,omitempty"`

	// BridgeReady is true when the runtime-evidence bridge Deployment
	// is available.
	// +optional
	BridgeReady bool `json:"bridgeReady,omitempty"`

	// Enforcing is true when kernel violations block (Enforce mode);
	// false during the observe baseline.
	// +optional
	Enforcing bool `json:"enforcing,omitempty"`
}

// InfrastructureStatus reports the state of operator-managed components.
type InfrastructureStatus struct {
	NATSReady          bool   `json:"natsReady,omitempty"`
	NATSVersion        string `json:"natsVersion,omitempty"`
	// NATSLeafConnected is true when the edge NATS leaf node has established
	// a connection to the datacenter hub (deployMode=edge only).
	NATSLeafConnected  bool   `json:"natsLeafConnected,omitempty"`
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
