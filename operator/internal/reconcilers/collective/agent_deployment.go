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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/manifests"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/templates"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

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

	configMapName := fmt.Sprintf("%s-acc-config", collective.Name)
	configMapLabels := util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, "acc-config", corpus.Spec.Version)
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      configMapName,
			Namespace: ns,
			Labels:    configMapLabels,
		},
		Data: map[string]string{"acc-config.yaml": accConfigYAML},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, cm, func(existing client.Object) error {
		existing.(*corev1.ConfigMap).Data = cm.Data
		return nil
	}); err != nil {
		return result, fmt.Errorf("upsert acc-config ConfigMap: %w", err)
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
	deployName := fmt.Sprintf("%s-%s", collective.Name, string(role))

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

	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deployName,
			Namespace: ns,
			Labels:    objectLabels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(roleSpec.Replicas),
			// Selector labels are immutable — keep the canonical agent set
			// only; objectLabels (which may include kagenti.io/type=agent)
			// ride on the metadata + pod template instead.
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: objectLabels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					// OCP restricted SCC — run as non-root UID 1001.
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: ptr.To(true),
						RunAsUser:    ptr.To(int64(1001)),
					},
					Containers: []corev1.Container{
						{
							Name:  "agent",
							Image: image,
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
					}, manifestVolumes...),
					// Append any role-specific VolumeClaimTemplates as emptyDir for Deployments
					// (StatefulSets would handle this differently; Deployments use PVC directly).
				},
			},
		},
	}

	// Proposal 011 PR-3 — inject the spiffe-helper sidecar + supporting
	// volumes/mounts/env when the collective has SPIFFE enabled.  No-op
	// otherwise.  Applied after the base Deployment is built so the
	// agent container is already at index 0.
	ApplySpiffeSidecar(deploy, collective, SpiffeHelperConfigMapName(collective))

	// Proposal 013 PR-4 — project this role's NATS NKey seed from the
	// operator-generated Secret + set the ACC_NKEY_* env vars.  No-op
	// when spec.infrastructure.nats.nkeyAuth is disabled.
	ApplyNKeySeed(deploy, corpus, role)

	upsertResult, err := util.Upsert(ctx, r.Client, r.Scheme, collective, deploy, func(existing client.Object) error {
		existingDeploy := existing.(*appsv1.Deployment)
		existingDeploy.Spec.Replicas = deploy.Spec.Replicas
		existingDeploy.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return 0, 0, false, fmt.Errorf("upsert Deployment %s: %w", deployName, err)
	}

	// Read the live Deployment to get ready replicas.
	liveDeploy := &appsv1.Deployment{}
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
