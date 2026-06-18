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

	// ImageRegistry is the LEGACY base registry for acc-agent-core and
	// infrastructure images, used only when ImageRepository is explicitly
	// set to "": images render as <imageRegistry>/<component>:<tag>.
	// Most clusters should leave this untouched and use ImageRepository.
	// +kubebuilder:default="registry.access.redhat.com"
	// +optional
	ImageRegistry string `json:"imageRegistry,omitempty"`

	// ImageRepository addresses every component within a single container
	// repository, distinguished by tag: images render as
	// <imageRepository>:<component>-<tag> (e.g.
	// quay.io/flg77/acc_images:acc-agent-core-0.1.0). The default is where
	// the ACC images are published; it is a PRIVATE repository — make sure
	// the Secret named in imagePullSecrets exists in this namespace. Set
	// explicitly to "" to fall back to the legacy ImageRegistry layout.
	// +kubebuilder:default="quay.io/flg77/acc_images"
	// +optional
	ImageRepository string `json:"imageRepository,omitempty"`

	// ImagePullSecrets is a list of Secret names used to pull the component
	// images. The operator adds them to the imagePullSecrets of every pod it
	// renders (agents, NATS, Redis, bridges, otel-collector). The default
	// matches the runbook's pull-secret name for the default ImageRepository
	// (a dangling reference is harmless — kubelet ignores missing secrets):
	//   oc -n <ns> create secret docker-registry acc-images-pull \
	//     --docker-server=quay.io --docker-username=<robot> --docker-password=<token>
	// +kubebuilder:default={"acc-images-pull"}
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
	// LEAVE THIS SECTION EMPTY while NATS is the active transport (the
	// default) — expanding it makes bootstrapServers required and, with no
	// Kafka installed, the bridge is skipped with a warning. RECOMMENDED FOR
	// LARGE AGENT FLEETS: at scale, Kafka is the preferred audit transport
	// (durable, high-throughput) over NATS-only. Kafka itself is NOT
	// installed by the operator — install AMQ Streams / Strimzi first
	// (OpenShift: the "Streams for Apache Kafka" Operator from OperatorHub),
	// then set bootstrapServers here.
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

	// RHOAI configures RHOAI-specific behaviour. Ignored unless deployMode
	// is rhoai. Leaving it empty applies the defaults (the namespace is
	// registered as a Data Science Project).
	// +optional
	RHOAI *RHOAISpec `json:"rhoai,omitempty"`

	// TUI deploys the acc-tui interaction surface (proposal 023 / ADR 025
	// interaction plane) — the power-user/ops terminal, reachable via
	// `oc rsh deploy/<corpus>-tui acc-tui`. Nil = not deployed. Secondary
	// to the webgui (the primary surface); the pod idles so the operator
	// can attach and run the TUI in the rsh TTY.
	// +optional
	TUI *TUISpec `json:"tui,omitempty"`

	// WebGUI deploys the acc-webgui interaction surface (proposal 023 / ADR
	// 025 interaction plane) behind a Keycloak-OIDC oauth2-proxy sidecar.
	// Nil = not deployed. The webhook materializes it (enabled) in rhoai
	// mode. An enabled WebGUI without a Keycloak block is NOT deployed —
	// the operator refuses to stand up an unauthenticated network surface
	// (ADR 025 §5); it sets a WebGUIBlocked status condition instead.
	// +optional
	WebGUI *WebGUISpec `json:"webgui,omitempty"`

	// BootstrapDefaultCatalog creates the canonical signed AccCatalog
	// (acc-canonical, the published ecosystem catalog) in this namespace
	// when NO AccCatalog exists there yet — so a fresh corpus is
	// package-ready out of the box (the Core+Assistant roles ship in the
	// agent image; the catalog enables role-package infusions on top).
	// CREATE-IF-ABSENT ONLY: the operator never updates an existing
	// catalog and never recreates one the user modified or deleted while
	// any other AccCatalog exists. Set false to manage catalogs entirely
	// yourself (e.g. GitOps).
	// +kubebuilder:default=true
	// +optional
	BootstrapDefaultCatalog *bool `json:"bootstrapDefaultCatalog,omitempty"`

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

// RHOAISpec configures how the corpus integrates with RHOAI (OpenShift AI).
type RHOAISpec struct {
	// RegisterNamespaceAsProject labels this corpus's namespace with
	// opendatahub.io/dashboard=true so it automatically appears in RHOAI
	// as a Data Science Project — models, pipelines, and workbenches then
	// wire into the same project naturally. ADDITIVE-ONLY: the operator
	// never removes the label (on corpus deletion or when this is later
	// set to false) because other RHOAI assets may live in the namespace.
	// +kubebuilder:default=true
	// +optional
	RegisterNamespaceAsProject *bool `json:"registerNamespaceAsProject,omitempty"`

	// ProjectDisplayName, when set, becomes the namespace's
	// openshift.io/display-name annotation (the name RHOAI shows for the
	// project). Leave empty to keep whatever display name the namespace
	// already has.
	// +kubebuilder:validation:MaxLength=128
	// +optional
	ProjectDisplayName string `json:"projectDisplayName,omitempty"`

	// DashboardNamespace is where the RHOAI dashboard reads OdhApplication
	// tiles and OdhQuickStart guides from (the CRDs are namespaced).
	// Defaults to "redhat-ods-applications" (RHOAI); set "opendatahub" on
	// upstream ODH installs.
	// +kubebuilder:default="redhat-ods-applications"
	// +optional
	DashboardNamespace string `json:"dashboardNamespace,omitempty"`
}

// TUISpec configures the acc-tui interaction surface (proposal 023 / ADR
// 025). By default the pod idles (attach with `oc rsh deploy/<corpus>-tui
// acc-tui`). Set webTerminal=true to expose the interactive TUI in a
// browser via ttyd behind the corpus's Keycloak oauth2-proxy + Route.
type TUISpec struct {
	// Enabled deploys the tui pod. Default true within the block; a nil
	// TUI block = not deployed.
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Replicas of the tui Deployment (usually 1 — it is an attach target,
	// not a scaled service).
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	// +optional
	Replicas int32 `json:"replicas,omitempty"`

	// WebTerminal exposes the interactive TUI in a browser via ttyd behind
	// a Keycloak oauth2-proxy + Route (proposal 023 §8 Q1). Default false —
	// the TUI is an `oc rsh` attach pod unless this is set. Requires
	// spec.webgui.keycloak (reused for auth); without it the operator
	// refuses to stand up an unauthenticated terminal (ADR 025 §5) and
	// falls back to the rsh-attach pod.
	// +optional
	WebTerminal *bool `json:"webTerminal,omitempty"`
}

// WebGUISpec configures the acc-webgui interaction surface (proposal 023 /
// ADR 025). The webgui is deployed behind an oauth2-proxy sidecar that runs
// the Keycloak OIDC auth-code flow; the webgui itself trusts the proxy's
// forwarded identity + groups and maps them to viewer/operator/publisher
// tiers. The webgui pod port is never exposed — only the proxy is.
type WebGUISpec struct {
	// Enabled deploys the webgui. Default true (the webhook materializes
	// this block enabled in rhoai mode). A nil block = not deployed.
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Replicas of the webgui Deployment.
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	// +optional
	Replicas int32 `json:"replicas,omitempty"`

	// Keycloak configures the OIDC login (via the oauth2-proxy sidecar).
	// REQUIRED for the webgui to actually deploy — without it the operator
	// refuses to stand up an unauthenticated surface (ADR 025 §5).
	// +optional
	Keycloak *WebGUIKeycloakSpec `json:"keycloak,omitempty"`

	// GroupMappings maps an ACC tier to the Keycloak group/role names that
	// grant it (rendered into ACC_WEBGUI_GROUP_MAPPINGS). Keys: "operator",
	// "publisher" (everything else is viewer). The canonical names live in
	// lab-gitops group_vars/rbac.yml (backlog 006). Example:
	// {"operator": "acc-operators", "publisher": "acc-publishers"}.
	// +optional
	GroupMappings map[string]string `json:"groupMappings,omitempty"`

	// Route exposes the webgui (the oauth2-proxy front) via an OpenShift
	// Route with edge TLS. Default true. Created only when the
	// route.openshift.io API is present (discovery-gated).
	// +kubebuilder:default=true
	// +optional
	Route *bool `json:"route,omitempty"`
}

// WebGUIKeycloakSpec is the Keycloak OIDC client config for the oauth2-proxy
// sidecar. The client secret + cookie secret are never inline — they come
// from a Secret in the corpus namespace.
type WebGUIKeycloakSpec struct {
	// IssuerURL is the Keycloak realm issuer, e.g.
	// https://keycloak.example.com/realms/acc.
	// +kubebuilder:validation:Pattern=`^https://.+/realms/.+`
	IssuerURL string `json:"issuerURL"`

	// ClientID is the Keycloak confidential client for the webgui. It is
	// also the OIDC audience the webgui validates (aud/azp).
	ClientID string `json:"clientID"`

	// ClientSecretName names a Secret in the corpus namespace holding the
	// Keycloak client secret under key "client-secret" and a cookie-signing
	// secret under key "cookie-secret".
	ClientSecretName string `json:"clientSecretName"`

	// GroupsClaim is the OIDC token claim carrying group names.
	// +kubebuilder:default="groups"
	// +optional
	GroupsClaim string `json:"groupsClaim,omitempty"`
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
	// hosts) is used. On RHOAI, prefer models ALREADY SERVED IN-CLUSTER
	// (list them: `oc get inferenceservice -A`) and reference them via
	// the AgentCollective's llm.vllm.inferenceServiceRef — in-cluster
	// traffic needs no entry here. Add FQDNs only for genuinely external
	// providers; do not carry over endpoints wired for standalone/edge
	// environments.
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
	// RECOMMENDED ON for RHOAI/OpenShift deployments: it extends Cat-A from
	// agent-runtime checks to cluster admission time. PREREQUISITE (manual):
	// install the "Gatekeeper Operator" from OperatorHub first (search
	// "Gatekeeper"; operator catalog: gatekeeper-operator-product) — the ACC
	// operator does NOT install it, it only syncs rules when present. Reasons
	// to leave it off: Gatekeeper not installed yet (synced templates would
	// have no controller), a cluster-wide admission-latency budget, or another
	// policy engine (Kyverno/ACS) already owns admission.
	// +kubebuilder:default=false
	GatekeeperIntegration bool `json:"gatekeeperIntegration"`

	// RuntimeEvidence configures the optional provider-agnostic
	// kernel-event evidence layer for Category-A governance (proposal
	// 015, security roadmap Phase 3).  When nil or disabled the
	// operator emits nothing and Cat-A stays metadata-only.
	// RECOMMENDED ON for RHOAI/datacenter deployments — Cat-A is the
	// constitutional floor and runtime evidence is what makes it
	// verifiable rather than declared. Deactivate only on hosts without
	// a runtime-security backend (RHACS/Falco/Tetragon/NetObserv) or in
	// resource-constrained edge profiles.
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
	// category_a.wasm blob to mount into each agent pod. WHAT THIS MEANS:
	// Cat-A is ACC's immutable constitutional layer — rules compiled from
	// Rego to WASM so every agent evaluates them in-process and cannot
	// mutate them at runtime. The default "acc-cat-a-wasm" expects the
	// ConfigMap the operator's governance assets publish; provide your own
	// ConfigMap to ship a customer constitution. Changing the blob requires
	// a pod roll (the mount is read-only by design). Pair with
	// gatekeeperIntegration for admission-time and runtimeEvidence for
	// kernel-event verification of the same tier.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:default="acc-cat-a-wasm"
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
	// before signing a Category C rule. Expressed as a decimal string. The
	// default 0.80 is a production-safe floor: lower values let the arbiter
	// adopt adaptive rules more eagerly, higher values keep Cat-C nearly
	// static.
	// +kubebuilder:validation:Pattern=`^0\.[0-9]+$|^1\.0+$`
	// +kubebuilder:default="0.80"
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
	// PREREQUISITE: an OTLP backend must be reachable at this address —
	// nothing on a fresh cluster provides one. Sandbox-friendly option:
	// the Tempo operator + a TempoMonolithic instance (PVC-backed, no S3),
	// e.g. tempo-acc-tempo.acc-observability.svc.cluster.local:4317.
	// If you do not need traces, set observability.backend to "log" instead.
	// +kubebuilder:validation:MinLength=1
	Endpoint string `json:"endpoint"`

	// Image overrides the OpenTelemetry Collector container image. Leave
	// empty for the operator's pinned default (mirrored into the ACC image
	// repository). Set this on disconnected clusters or to use your own
	// mirror of opentelemetry-collector-contrib.
	// +optional
	Image string `json:"image,omitempty"`

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

	// RHOAIProjectRegistered is true once the corpus namespace carries the
	// opendatahub.io/dashboard label (visible as an RHOAI Data Science
	// Project).
	// +optional
	RHOAIProjectRegistered bool `json:"rhoaiProjectRegistered,omitempty"`

	// WebGUIDeployed is true once the acc-webgui Deployment + Service (and,
	// when route.openshift.io is present, Route) are reconciled behind the
	// Keycloak oauth2-proxy (proposal 023). False when spec.webgui is unset,
	// disabled, or its Keycloak config is incomplete (the operator refuses
	// to expose an unauthenticated surface).
	// +optional
	WebGUIDeployed bool `json:"webguiDeployed,omitempty"`

	// TUIDeployed is true once the acc-tui attach pod is reconciled
	// (proposal 023). False when spec.tui is unset or disabled.
	// +optional
	TUIDeployed bool `json:"tuiDeployed,omitempty"`

	// WebGUIURL is the external https URL of the webgui Route once
	// admitted (also surfaced as an app-launcher ConsoleLink). Empty
	// until the Route gets an ingress host.
	// +optional
	WebGUIURL string `json:"webguiURL,omitempty"`

	// TUIURL is the external https URL of the acc-tui web-terminal Route
	// once admitted (proposal 023 ttyd web-terminal). Empty until then.
	// +optional
	TUIURL string `json:"tuiURL,omitempty"`

	// DefaultCatalogBootstrapped is true once the operator created the
	// acc-canonical AccCatalog in this namespace (or found catalogs
	// already present).
	// +optional
	DefaultCatalogBootstrapped bool `json:"defaultCatalogBootstrapped,omitempty"`

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

	// SharedModel surfaces the KServe model this collective consumes and
	// whether it is shared in from another namespace (e.g. the acc-system
	// shared vLLM — proposal 026 G1), so the oversight plane can show
	// "model: shared from <ns>". Nil for non-KServe backends
	// (anthropic/ollama/llama_stack).
	// +optional
	SharedModel *SharedModelStatus `json:"sharedModel,omitempty"`
}

// SharedModelStatus describes the KServe model a collective consumes and
// whether it is consumed cross-namespace (shared). Proposal 026 G1.
type SharedModelStatus struct {
	// InferenceService is the consumed KServe InferenceService name.
	// +optional
	InferenceService string `json:"inferenceService,omitempty"`

	// Namespace is where that InferenceService lives.
	// +optional
	Namespace string `json:"namespace,omitempty"`

	// Shared is true when Namespace differs from the collective's own
	// namespace — the model is shared in from another Data Science Project
	// (e.g. acc-system) rather than served locally.
	// +optional
	Shared bool `json:"shared,omitempty"`

	// URL is the resolved model endpoint (empty until the InferenceService
	// publishes one).
	// +optional
	URL string `json:"url,omitempty"`
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
