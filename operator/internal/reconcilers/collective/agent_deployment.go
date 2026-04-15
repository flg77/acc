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

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
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
func (r *AgentDeploymentReconciler) ReconcileCollective(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
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
	// One Deployment per agent role.
	// -----------------------------------------------------------------------
	for _, roleSpec := range collective.Spec.Agents {
		ready, desired, progressing, err := r.reconcileRoleDeployment(ctx, corpus, collective, roleSpec, configMapName, ns)
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
	configMapName, ns string,
) (ready, desired int32, progressing bool, err error) {
	role := roleSpec.Role
	labels := util.AgentLabels(corpus.Name, collective.Spec.CollectiveID, role, corpus.Spec.Version)
	deployName := fmt.Sprintf("%s-%s", collective.Name, string(role))

	// Resolve Anthropic API key env var if needed.
	extraEnv := buildExtraEnv(corpus, collective, roleSpec)

	image := fmt.Sprintf("%s/acc-agent-core:%s", corpus.Spec.ImageRegistry, corpus.Spec.Version)

	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deployName,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(roleSpec.Replicas),
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					// OCP restricted SCC — run as non-root UID 1001.
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: ptr.To(true),
						RunAsUser:    ptr.To(int64(1001)),
					},
					Containers: []corev1.Container{
						{
							Name:  "agent",
							Image: image,
							Env:   append([]corev1.EnvVar{
								{Name: "ACC_AGENT_ROLE", Value: string(role)},
								{Name: "ACC_COLLECTIVE_ID", Value: collective.Spec.CollectiveID},
								{Name: "ACC_CORPUS_NAME", Value: corpus.Name},
								{Name: "ACC_CONFIG_PATH", Value: "/etc/acc/acc-config.yaml"},
							}, extraEnv...),
							Resources: derefResources(roleSpec.Resources),
							VolumeMounts: []corev1.VolumeMount{
								{Name: "acc-config", MountPath: "/etc/acc"},
								{Name: "wasm-governance", MountPath: "/etc/acc/governance"},
							},
						},
					},
					Volumes: []corev1.Volume{
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
					},
					// Append any role-specific VolumeClaimTemplates as emptyDir for Deployments
					// (StatefulSets would handle this differently; Deployments use PVC directly).
				},
			},
		},
	}

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
// API key reference when the LLM backend is anthropic.
func buildExtraEnv(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	roleSpec accv1alpha1.AgentRoleSpec,
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
