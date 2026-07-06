// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"context"
	"fmt"
	"sort"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/manifests"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/sandbox"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/templates"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// Proposal 024 — agent StatefulSet persistence.
const (
	// dataVolumeName is the per-pod PVC (VolumeClaimTemplate) mounted at
	// /app/data: the embedded vector store + SQLite records live here.
	dataVolumeName = "acc-data"
	// agentDataVolumeSize is the default per-agent PVC request.  Sized for
	// a quantized turbovec corpus (~192 B/vector at 4-bit) plus the SQLite
	// record store + LanceDB fallback; ample for demo/edge workloads.
	agentDataVolumeSize = "2Gi"
)

// agentHeadlessServiceName is the governing (headless) Service every agent
// StatefulSet in a collective references via spec.serviceName.  One per
// collective; created by ReconcileCollective.  Agents are NATS clients
// (outbound), so stable inbound DNS is nominal — the Service exists to
// satisfy the StatefulSet contract and give pods stable identities.
func agentHeadlessServiceName(collective *accv1alpha1.AgentCollective) string {
	return fmt.Sprintf("%s-agents", collective.Name)
}

// AgentDeploymentResult carries per-role ready/desired counts back to the
// parent CollectiveReconciler so it can compute the collective phase.
type AgentDeploymentResult struct {
	ReadyAgents   map[string]int32
	DesiredAgents map[string]int32
	Progressing   bool
}

// AgentDeploymentReconciler manages one Deployment per agent role for a given
// AgentCollective. It also owns the acc-config.yaml ConfigMap mounted into
// each agent pod.
type AgentDeploymentReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// ReconcileCollective reconciles all role Deployments for one collective.
// roleConfigMapName is the name of the acc-role-{collectiveId} ConfigMap
// created by CollectiveReconciler.reconcileRoleConfigMap (ACC-6a REQ-OP-003).
// inferenceURL is the model endpoint resolved by KServeReconciler from the
// referenced InferenceService status (empty when not yet published).
func (r *AgentDeploymentReconciler) ReconcileCollective(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	roleConfigMapName string,
	inferenceURL string,
) (AgentDeploymentResult, error) {
	ns := corpus.Namespace
	result := AgentDeploymentResult{
		ReadyAgents:   make(map[string]int32),
		DesiredAgents: make(map[string]int32),
	}

	// -----------------------------------------------------------------------
	// Render and upsert the acc-config.yaml ConfigMap for this collective.
	// -----------------------------------------------------------------------
	accConfigYAML, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		return result, fmt.Errorf("render acc-config: %w", err)
	}

	// Deliver the namespace's AccCatalogs (rendered into the acc-catalogs
	// ConfigMap by AccCatalogReconciler) as /etc/acc/catalogs.yaml via the SAME
	// acc-config mount the agent already reads. Merging into acc-config avoids a
	// nested subPath file mount into the /etc/acc ConfigMap volume, which does
	// NOT materialize reliably (the file is absent in-pod even though the mount
	// renders in the Deployment) — proposal 032 §11 catalog delivery.
	cmData := map[string]string{"acc-config.yaml": accConfigYAML}
	catCM := &corev1.ConfigMap{}
	if getErr := r.Client.Get(ctx, types.NamespacedName{Namespace: ns, Name: "acc-catalogs"}, catCM); getErr == nil {
		if cy := catCM.Data["catalogs.yaml"]; cy != "" {
			cmData["catalogs.yaml"] = cy
		}
	}

	configMapName := fmt.Sprintf("%s-acc-config", collective.Name)
	configMapLabels := util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, "acc-config", corpus.Spec.Version)
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      configMapName,
			Namespace: ns,
			Labels:    configMapLabels,
		},
		Data: cmData,
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, cm, func(existing client.Object) error {
		existing.(*corev1.ConfigMap).Data = cm.Data
		return nil
	}); err != nil {
		return result, fmt.Errorf("upsert acc-config ConfigMap: %w", err)
	}

	// -----------------------------------------------------------------------
	// Headless governing Service for the agent StatefulSets (proposal 024).
	// One per collective; selects every agent pod by collective id.  Agents
	// talk OUT to NATS, so this is for StatefulSet pod identity, not inbound
	// traffic — hence ClusterIP None + publishNotReadyAddresses.
	// -----------------------------------------------------------------------
	headlessName := agentHeadlessServiceName(collective)
	headlessLabels := util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, "agents", corpus.Spec.Version)
	headlessSvc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      headlessName,
			Namespace: ns,
			Labels:    headlessLabels,
		},
		Spec: corev1.ServiceSpec{
			ClusterIP:                corev1.ClusterIPNone,
			PublishNotReadyAddresses: true,
			Selector:                 map[string]string{accv1alpha1.LabelCollectiveID: collective.Spec.CollectiveID},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, headlessSvc, func(existing client.Object) error {
		// ClusterIP is immutable; only re-assert selector + labels.
		existing.(*corev1.Service).Spec.Selector = headlessSvc.Spec.Selector
		return nil
	}); err != nil {
		return result, fmt.Errorf("upsert agent headless Service: %w", err)
	}

	// -----------------------------------------------------------------------
	// SPIFFE helper.conf ConfigMap (proposal 011 PR-3) — only when the
	// collective opted into SPIFFE.  Mounted into the spiffe-helper
	// sidecar that ApplySpiffeSidecar() adds to each agent pod.
	// -----------------------------------------------------------------------
	if SpiffeEnabled(collective) {
		helperCM := &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{
				Name:      SpiffeHelperConfigMapName(collective),
				Namespace: ns,
				Labels: util.CollectiveLabels(
					corpus.Name, collective.Spec.CollectiveID,
					"spiffe-helper", corpus.Spec.Version,
				),
			},
			Data: map[string]string{spiffeHelperConfigKey: RenderSpiffeHelperConfig()},
		}
		if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, helperCM, func(existing client.Object) error {
			existing.(*corev1.ConfigMap).Data = helperCM.Data
			return nil
		}); err != nil {
			return result, fmt.Errorf("upsert spiffe-helper ConfigMap: %w", err)
		}
	}

	// -----------------------------------------------------------------------
	// One Deployment per agent role.
	// -----------------------------------------------------------------------
	for _, roleSpec := range collective.Spec.Agents {
		ready, desired, progressing, err := r.reconcileRoleDeployment(ctx, corpus, collective, roleSpec, configMapName, roleConfigMapName, ns, inferenceURL)
		if err != nil {
			return result, err
		}
		roleName := string(roleSpec.Role)
		result.ReadyAgents[roleName] = ready
		result.DesiredAgents[roleName] = desired
		if progressing {
			result.Progressing = true
		}
	}

	return result, nil
}

// AgentPodSecurityContext returns the pod-level SecurityContext for
// operator-rendered agent pods.
//
// It is deliberately OpenShift restricted-v2 SCC compatible: it does NOT pin
// runAsUser (nor runAsGroup / fsGroup). On OpenShift the namespace's
// openshift.io/sa.scc.uid-range annotation supplies a per-namespace UID and
// the SCC admission plugin injects it; hardcoding runAsUser: 1001 makes
// restricted-v2 reject every pod ("must be in the ranges: [1000920000, ...]")
// and zero agent pods are created (live RHOAI 3.4 finding).
//
// The acc-agent-core image (UBI10, USER 1001, GID 0, `chmod -R g=u /app`)
// tolerates an arbitrary high UID because all app dirs are group-0 writable
// and OpenShift always runs with GID 0 under restricted-v2. On vanilla
// Kubernetes (no UID injection) the container falls back to the image's own
// USER 1001 — also fine. Keeping runAsNonRoot: true preserves the non-root
// guarantee on both platforms without conflicting with the SCC.
func AgentPodSecurityContext() *corev1.PodSecurityContext {
	return &corev1.PodSecurityContext{
		RunAsNonRoot:   ptr.To(true),
		SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
	}
}

// AgentContainerSecurityContext returns the container-level SecurityContext for
// operator-rendered agent containers. It drops ALL capabilities and disallows
// privilege escalation, satisfying restricted-v2 while — crucially — NOT
// pinning runAsUser, so the SCC-injected namespace UID is honored.
func AgentContainerSecurityContext() *corev1.SecurityContext {
	return &corev1.SecurityContext{
		RunAsNonRoot:             ptr.To(true),
		AllowPrivilegeEscalation: ptr.To(false),
		Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
		SeccompProfile:           &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
	}
}

func (r *AgentDeploymentReconciler) reconcileRoleDeployment(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	roleSpec accv1alpha1.AgentRoleSpec,
	configMapName, roleConfigMapName, ns string,
	inferenceURL string,
) (ready, desired int32, progressing bool, err error) {
	role := roleSpec.Role
	labels := util.AgentLabels(corpus.Name, collective.Spec.CollectiveID, role, corpus.Spec.Version)
	// objectLabels apply to ObjectMeta + pod-template labels (NOT the
	// selector — selector labels are immutable).  When Kagenti AgentCard
	// auto-discovery is opted in (OpenSpec 20260527-agentcard-discovery,
	// Phase 1) AgentObjectLabels merges in `kagenti.io/type: agent`.
	// Default off → objectLabels == labels and existing collectives are
	// unchanged.  See kagenti.go.
	objectLabels := AgentObjectLabels(collective, labels)
	// Deployment name sanitizes underscores → hyphens (RFC-1123). Use the
	// shared helper so the AgentCollective status controller resolves the
	// EXACT same name (proposal 032 Finding A) — labels keep the raw role.
	deployName := util.AgentDeploymentName(collective.Name, string(role))

	// Resolve Anthropic API key env var if needed.
	extraEnv := buildExtraEnv(corpus, collective, roleSpec, inferenceURL)

	// Manifest delivery (PR-51): build the three roles/skills/mcps volumes
	// and items[] projections from the corpus-scoped ConfigMaps emitted by
	// ManifestDeliveryReconciler. Returns empty slices when delivery is
	// disabled (spec.manifestDelivery == "none") or when a CM is not yet
	// present (next reconcile cycle picks them up — manifest delivery runs
	// first in the parent chain, so this is rare in practice).
	manifestMounts, manifestVolumes, manifestEnv, err := r.buildManifestDelivery(ctx, corpus, ns)
	if err != nil {
		return 0, 0, false, fmt.Errorf("build manifest delivery for %s: %w", deployName, err)
	}

	image := util.ComponentImage(corpus, "acc-agent-core", corpus.Spec.Version)

	// Proposal 024 — agents are StatefulSets so each replica gets its own
	// PVC (VolumeClaimTemplates) for the embedded vector store + records.
	// turbovec is single-writer (one index per pod), so a shared RWO PVC
	// would deadlock replica #2; per-replica volumes are the correct shape
	// and also give LanceDB durable persistence (it writes /app/data too).
	// The headless governing Service is created once per collective in
	// ReconcileCollective.
	deploy := &appsv1.StatefulSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deployName,
			Namespace: ns,
			Labels:    objectLabels,
		},
		Spec: appsv1.StatefulSetSpec{
			Replicas:    ptr.To(roleSpec.Replicas),
			ServiceName: agentHeadlessServiceName(collective),
			// Selector labels are immutable — keep the canonical agent set
			// only; objectLabels (which may include kagenti.io/type=agent)
			// ride on the metadata + pod template instead.  SelectorLabels
			// narrows to the stable subset so a corpus version bump doesn't
			// mutate the immutable selector (proposal 032 stable-selector fix).
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			VolumeClaimTemplates: []corev1.PersistentVolumeClaim{
				{
					ObjectMeta: metav1.ObjectMeta{Name: dataVolumeName},
					Spec: corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
						Resources: corev1.VolumeResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceStorage: resource.MustParse(agentDataVolumeSize),
							},
						},
					},
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: objectLabels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					// OpenShift restricted-v2 SCC compatible: no hardcoded
					// runAsUser — the namespace UID range is injected by the
					// SCC admission plugin. See AgentPodSecurityContext
					// (also sets the RuntimeDefault seccomp profile).
					SecurityContext: AgentPodSecurityContext(),
					Containers: []corev1.Container{
						{
							Name:            "agent",
							Image:           image,
							SecurityContext: AgentContainerSecurityContext(),
							Env: append(append([]corev1.EnvVar{
								{Name: "ACC_AGENT_ROLE", Value: string(role)},
								{Name: "ACC_COLLECTIVE_ID", Value: collective.Spec.CollectiveID},
								{Name: "ACC_CORPUS_NAME", Value: corpus.Name},
								{Name: "ACC_CONFIG_PATH", Value: "/etc/acc/acc-config.yaml"},
								// Downward-API pod identity (proposal 015) — lets the
								// agent filter KERNEL_EVENT signals to its own pod.
								{
									Name: "ACC_POD_NAME",
									ValueFrom: &corev1.EnvVarSource{
										FieldRef: &corev1.ObjectFieldSelector{
											FieldPath: "metadata.name",
										},
									},
								},
								{
									Name: "ACC_POD_UID",
									ValueFrom: &corev1.EnvVarSource{
										FieldRef: &corev1.ObjectFieldSelector{
											FieldPath: "metadata.uid",
										},
									},
								},
							}, manifestEnv...), extraEnv...),
							Resources: derefResources(roleSpec.Resources),
							VolumeMounts: append([]corev1.VolumeMount{
								{Name: "acc-config", MountPath: "/etc/acc"},
								{Name: "wasm-governance", MountPath: "/etc/acc/governance"},
								// ACC-6a: role definition mounted read-only at /app/acc-role.yaml
								{
									Name:      "acc-role",
									MountPath: "/app/acc-role.yaml",
									SubPath:   "acc-role.yaml",
									ReadOnly:  true,
								},
								// Proposal 024 — durable agent data dir.  The
								// embedded vector store lives here (turbovec
								// .tvim + records.db, or lancedb); backed by the
								// per-pod PVC from VolumeClaimTemplates below so
								// episodic memory + the RAG corpus survive
								// restarts.  Milvus-backed agents simply leave it
								// near-empty.
								{Name: dataVolumeName, MountPath: "/app/data"},
								// Writable packages root for AccPackageInstall.
								// The agent runs non-root (SCC-injected UID, GID 0)
								// and acc-pkg unpacks installed packs under
								// /var/lib/acc, which is not writable in the image
								// layer (PermissionError on a signed-pack install).
								// Back it with an emptyDir — ephemeral is fine: the
								// operator re-reconciles AccPackageInstall after a
								// pod restart (032 §11 tail / proposal 034 §8-Q3).
								{Name: "acc-packages", MountPath: "/var/lib/acc"},
							}, manifestMounts...),
						},
					},
					Volumes: append([]corev1.Volume{
						{
							Name: "acc-config",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{Name: configMapName},
								},
							},
						},
						{
							Name: "wasm-governance",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: corpus.Spec.Governance.CategoryA.WASMConfigMapRef,
									},
								},
							},
						},
						// ACC-6a: role definition ConfigMap (REQ-OP-003)
						{
							Name: "acc-role",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: roleConfigMapName,
									},
								},
							},
						},
						// Writable packages root (see the acc-packages VolumeMount).
						{
							Name:         "acc-packages",
							VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
						},
					}, manifestVolumes...),
					// Per-replica /app/data PVC is the StatefulSet's
					// VolumeClaimTemplate above — no extra Volume entry here.
				},
			},
		},
	}

	// Proposal 011 PR-3 — inject the spiffe-helper sidecar + supporting
	// volumes/mounts/env when the collective has SPIFFE enabled.  No-op
	// otherwise.  Applied after the base pod template is built so the
	// agent container is already at index 0.
	ApplySpiffeSidecar(&deploy.Spec.Template, collective, SpiffeHelperConfigMapName(collective))

	// Proposal 013 PR-4 — project this role's NATS NKey seed from the
	// operator-generated Secret + set the ACC_NKEY_* env vars.  No-op
	// when spec.infrastructure.nats.nkeyAuth is disabled.
	ApplyNKeySeed(&deploy.Spec.Template, corpus, role)

	// OpenShell Model 2 (proposal 051): when this corpus opts into
	// kernel-enforced sandboxing AND a Gateway is configured, the agent stays a
	// normal StatefulSet but gains a per-agent OpenShell sandbox — an
	// `openshell sandbox create` initContainer carrying the corpus's Cat-A/B/C
	// policy (BuildSandboxPolicyYAML) + the env the runtime shim (acc.sandbox)
	// reads to delegate code execution INTO the cage. Inert until GatewayURL is
	// set; fail-closed (D3) — a policy-ConfigMap error aborts, so no agent runs
	// without its cage. (Model 1 emitted the agent AS a Sandbox CR here; a raw
	// CR gets no gateway-injected supervisor → uncaged, so delivery moved to
	// runtime exec-delegation and BuildSandboxObject is now caller-less.)
	if sandbox.SandboxWorkloadActive(corpus) {
		policyCMName := sandbox.PolicyConfigMapName(deployName)
		policyCM, err := sandbox.BuildPolicyConfigMap(corpus, policyCMName, ns)
		if err != nil {
			return 0, roleSpec.Replicas, false, fmt.Errorf("build sandbox policy for %s: %w", deployName, err)
		}
		if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, policyCM, func(existing client.Object) error {
			existing.(*corev1.ConfigMap).Data = policyCM.Data
			return nil
		}); err != nil {
			return 0, roleSpec.Replicas, false, fmt.Errorf("upsert sandbox policy %s: %w", policyCMName, err)
		}
		sandbox.ApplyOpenShellSandbox(&deploy.Spec.Template, corpus, sandbox.SandboxName(deployName), policyCMName, image)
	}

	// Upsert: Replicas + Template are mutable on a StatefulSet;
	// VolumeClaimTemplates + ServiceName are immutable post-create, so the
	// reconcile closure deliberately does NOT touch them (a forbidden-field
	// patch would otherwise fail the update).
	upsertResult, err := util.Upsert(ctx, r.Client, r.Scheme, collective, deploy, func(existing client.Object) error {
		existingSts := existing.(*appsv1.StatefulSet)
		existingSts.Spec.Replicas = deploy.Spec.Replicas
		existingSts.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return 0, 0, false, fmt.Errorf("upsert StatefulSet %s: %w", deployName, err)
	}

	// Read the live StatefulSet to get ready replicas.
	liveDeploy := &appsv1.StatefulSet{}
	if err := r.Client.Get(ctx, types.NamespacedName{Namespace: ns, Name: deployName}, liveDeploy); err != nil {
		return 0, roleSpec.Replicas, true, nil
	}

	readyReplicas := liveDeploy.Status.ReadyReplicas
	desiredReplicas := roleSpec.Replicas
	isProgressing := upsertResult != util.UpsertResultNoop || readyReplicas < desiredReplicas

	return readyReplicas, desiredReplicas, isProgressing, nil
}

// buildExtraEnv appends the role-specific ExtraEnv and injects the Anthropic
// API key reference when the LLM backend is anthropic. inferenceURL is the
// vLLM model endpoint resolved from the referenced InferenceService status;
// the rendered acc-config.yaml carries the ${ACC_VLLM_INFERENCE_URL}
// placeholder that this env var satisfies.
func buildExtraEnv(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	roleSpec accv1alpha1.AgentRoleSpec,
	inferenceURL string,
) []corev1.EnvVar {
	var envs []corev1.EnvVar

	// Inject Anthropic API key from Secret reference.
	if collective.Spec.LLM.Backend == accv1alpha1.LLMBackendAnthropic {
		if llm := collective.Spec.LLM.Anthropic; llm != nil {
			envs = append(envs, corev1.EnvVar{
				Name: "ACC_ANTHROPIC_API_KEY",
				ValueFrom: &corev1.EnvVarSource{
					SecretKeyRef: &llm.APIKeySecretRef,
				},
			})
		}
	}

	// Inject the resolved vLLM endpoint so the agent's config placeholder
	// resolves (env overrides config: ACC_VLLM_INFERENCE_URL → llm.vllm_inference_url).
	if collective.Spec.LLM.Backend == accv1alpha1.LLMBackendVLLM && inferenceURL != "" {
		envs = append(envs, corev1.EnvVar{
			Name:  "ACC_VLLM_INFERENCE_URL",
			Value: inferenceURL,
		})
	}

	// Kafka credentials if configured.
	if kafka := corpus.Spec.Kafka; kafka != nil && kafka.CredentialsSecretRef != nil {
		envs = append(envs,
			corev1.EnvVar{
				Name: "ACC_KAFKA_SASL_USERNAME",
				ValueFrom: &corev1.EnvVarSource{
					SecretKeyRef: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: kafka.CredentialsSecretRef.Name},
						Key:                  "kafka_sasl_username",
					},
				},
			},
			corev1.EnvVar{
				Name: "ACC_KAFKA_SASL_PASSWORD",
				ValueFrom: &corev1.EnvVarSource{
					SecretKeyRef: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: kafka.CredentialsSecretRef.Name},
						Key:                  "kafka_sasl_password",
					},
				},
			},
		)
	}

	// Runtime-evidence / kernel-event Cat-A (proposal 015).  When the
	// corpus has runtimeEvidence enabled, the agent's CognitiveCore
	// subscribes to KERNEL_EVENT and folds kernel evidence into Cat-A.
	if re := corpus.Spec.Governance.RuntimeEvidence; re != nil && re.Enabled {
		envs = append(envs,
			corev1.EnvVar{Name: "ACC_RUNTIME_EVIDENCE_ENABLED", Value: "true"},
			corev1.EnvVar{Name: "ACC_RUNTIME_ENFORCE", Value: fmt.Sprintf("%t", re.Enforce)},
		)
	}

	// Role-specific extra env.
	envs = append(envs, roleSpec.ExtraEnv...)
	return envs
}

func derefResources(r *corev1.ResourceRequirements) corev1.ResourceRequirements {
	if r != nil {
		return *r
	}
	return corev1.ResourceRequirements{}
}

// buildManifestDelivery returns the VolumeMount/Volume/EnvVar slices that
// inject the corpus-scoped acc-roles, acc-skills, and acc-mcps ConfigMaps
// into agent pods at /etc/acc/{roles,skills,mcps} (with the matching
// ACC_*_ROOT env vars).
//
// Each Volume uses an explicit items[] projection so the flattened
// ConfigMap keys (path__separated__like__this) re-project to slash-paths
// in the pod's filesystem. The keys are read from the live ConfigMap so
// the projection always matches the data — no separate source of truth.
//
// When spec.manifestDelivery == "none" or any expected ConfigMap is not
// yet present, returns empty slices and a nil error. The reconciler will
// retry on the next cycle once ManifestDeliveryReconciler has emitted
// the CMs.
func (r *AgentDeploymentReconciler) buildManifestDelivery(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	ns string,
) ([]corev1.VolumeMount, []corev1.Volume, []corev1.EnvVar, error) {
	if corpus.Spec.ManifestDelivery == "none" {
		return nil, nil, nil, nil
	}

	rolesSuffix, skillsSuffix, mcpsSuffix := manifests.Suffixes()

	plans := []struct {
		volumeName string
		cmSuffix   string
		mountPath  string
		envVarName string
	}{
		{"acc-roles", rolesSuffix, manifests.RolesMountPath, "ACC_ROLES_ROOT"},
		{"acc-skills", skillsSuffix, manifests.SkillsMountPath, "ACC_SKILLS_ROOT"},
		{"acc-mcps", mcpsSuffix, manifests.MCPsMountPath, "ACC_MCPS_ROOT"},
	}

	var (
		mounts  []corev1.VolumeMount
		volumes []corev1.Volume
		envs    []corev1.EnvVar
	)
	for _, p := range plans {
		cmName := manifests.ConfigMapName(corpus, p.cmSuffix)
		cm := &corev1.ConfigMap{}
		if err := r.Client.Get(ctx, types.NamespacedName{Namespace: ns, Name: cmName}, cm); err != nil {
			// CM not yet present — skip this tree; next reconcile picks it up.
			// Do not error: the manifest reconciler runs in a separate slot of
			// the parent chain and may not have completed on first apply.
			continue
		}
		items := ProjectManifestItems(cm.Data)
		mounts = append(mounts, corev1.VolumeMount{
			Name:      p.volumeName,
			MountPath: p.mountPath,
			ReadOnly:  true,
		})
		volumes = append(volumes, corev1.Volume{
			Name: p.volumeName,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: cmName},
					Items:                items,
				},
			},
		})
		envs = append(envs, corev1.EnvVar{Name: p.envVarName, Value: p.mountPath})
	}
	return mounts, volumes, envs, nil
}

// ProjectManifestItems renders a manifest ConfigMap's data into the volume
// projection items, sorted by Key. Iterating the map directly yields Go's
// randomized order, which makes the rendered pod template differ on every
// reconcile → the Deployment is patched each pass → perpetual ReplicaSet
// churn. Exported so the determinism regression test can pin the contract.
func ProjectManifestItems(data map[string]string) []corev1.KeyToPath {
	items := make([]corev1.KeyToPath, 0, len(data))
	for key := range data {
		items = append(items, corev1.KeyToPath{
			Key:  key,
			Path: manifests.UnflattenKey(key),
		})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Key < items[j].Key })
	return items
}
