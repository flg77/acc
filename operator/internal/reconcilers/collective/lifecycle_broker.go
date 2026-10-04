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
	"strconv"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// Lifecycle broker (OpenSpec 20261003-assistant-orchestrated-infusion,
// phase 6).  The assistant may start, stop and pause the collective's
// specialist roles; on a cluster that means changing spec.agents[].replicas
// on its own AgentCollective.  The assistant never holds that power: an
// approved PROPOSE_LIFECYCLE becomes an arbiter-signed request, and this
// broker -- a separate pod whose ServiceAccount may get and patch exactly
// one object -- verifies it and patches.  The decisions (closed vocabulary,
// control roles never touched, busy roles never stopped, KEDA-scaled
// collectives refused, per-role cap) are in the runtime's acc/lifecycle.py;
// the RBAC here is the backstop if the broker itself were compromised.

const (
	lifecycleBrokerComponent = "lifecycle-broker"
	// lifecycleBrokerIdentity is the NKey identity (templates.nkeyIdentities).
	lifecycleBrokerIdentity           = "lifecycle_broker"
	defaultLifecycleMaxReplicas int32 = 3
)

// LifecycleBrokerName is the broker's Deployment, ServiceAccount, Role and
// RoleBinding name for a collective.
func LifecycleBrokerName(collective *accv1alpha1.AgentCollective) string {
	return fmt.Sprintf("%s-lifecycle-broker", collective.Name)
}

func lifecycleEnabled(collective *accv1alpha1.AgentCollective) bool {
	return collective.Spec.Lifecycle != nil && collective.Spec.Lifecycle.Enabled
}

// LifecycleBrokerRules is everything the broker may ask the cluster: get and
// patch on its own AgentCollective, by name.  No list (it cannot discover
// other collectives), no other kind, no other verb.
func LifecycleBrokerRules(collective *accv1alpha1.AgentCollective) []rbacv1.PolicyRule {
	return []rbacv1.PolicyRule{{
		APIGroups:     []string{accv1alpha1.GroupVersion.Group},
		Resources:     []string{"agentcollectives"},
		ResourceNames: []string{collective.Name},
		Verbs:         []string{"get", "patch"},
	}}
}

// LifecycleBrokerDeployment renders the broker Deployment (pure, for tests).
func LifecycleBrokerDeployment(corpus *accv1alpha1.AgentCorpus, collective *accv1alpha1.AgentCollective) *appsv1.Deployment {
	name := LifecycleBrokerName(collective)
	labels := util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, lifecycleBrokerComponent, corpus.Spec.Version)
	maxReplicas := defaultLifecycleMaxReplicas
	if lc := collective.Spec.Lifecycle; lc != nil && lc.MaxReplicas > 0 {
		maxReplicas = lc.MaxReplicas
	}
	env := []corev1.EnvVar{
		{Name: "ACC_COLLECTIVE_ID", Value: collective.Spec.CollectiveID},
		{Name: "ACC_NATS_URL", Value: fmt.Sprintf("nats://%s-nats.%s.svc.cluster.local:4222", corpus.Name, corpus.Namespace)},
		{Name: "ACC_LIFECYCLE_RUNTIME", Value: "kubernetes"},
		{Name: "ACC_COLLECTIVE_CR_NAME", Value: collective.Name},
		{Name: "ACC_LIFECYCLE_MAX_REPLICAS", Value: strconv.Itoa(int(maxReplicas))},
		{Name: "ACC_NAMESPACE", ValueFrom: &corev1.EnvVarSource{
			FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.namespace"},
		}},
	}
	if lc := collective.Spec.Lifecycle; lc != nil && lc.VerifyKey != nil {
		env = append(env, corev1.EnvVar{Name: "ACC_ARBITER_VERIFY_KEY", ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: lc.VerifyKey.DeepCopy(),
		}})
	}
	tmpl := corev1.PodTemplateSpec{
		ObjectMeta: metav1.ObjectMeta{Labels: labels},
		Spec: corev1.PodSpec{
			ServiceAccountName: name,
			ImagePullSecrets:   util.ImagePullSecrets(corpus),
			SecurityContext:    AgentPodSecurityContext(),
			Containers: []corev1.Container{{
				Name:            "broker",
				Image:           util.ComponentImage(corpus, "acc-agent-core", corpus.Spec.Version),
				Command:         []string{"python3", "-m", "acc.lifecycle_broker"},
				SecurityContext: AgentContainerSecurityContext(),
				Env:             env,
			}},
		},
	}
	ApplyNKeySeed(&tmpl, corpus, accv1alpha1.AgentRole(lifecycleBrokerIdentity))
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: collective.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(int32(1)),
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			Template: tmpl,
		},
	}
}

// LifecycleBrokerReconciler upserts (or, when disabled, removes) a
// collective's broker and its RBAC.
type LifecycleBrokerReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// ReconcileCollective brings the broker in line with spec.lifecycle.
func (r *LifecycleBrokerReconciler) ReconcileCollective(
	ctx context.Context, corpus *accv1alpha1.AgentCorpus, collective *accv1alpha1.AgentCollective,
) error {
	name := LifecycleBrokerName(collective)
	ns := collective.Namespace
	labels := util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, lifecycleBrokerComponent, corpus.Spec.Version)
	meta := metav1.ObjectMeta{Name: name, Namespace: ns, Labels: labels}

	if !lifecycleEnabled(collective) {
		// Turning the feature off removes the power, not just the pod.
		for _, obj := range []client.Object{
			&appsv1.Deployment{ObjectMeta: meta},
			&rbacv1.RoleBinding{ObjectMeta: meta},
			&rbacv1.Role{ObjectMeta: meta},
			&corev1.ServiceAccount{ObjectMeta: meta},
		} {
			if err := r.Client.Delete(ctx, obj); err != nil && !apierrors.IsNotFound(err) {
				return fmt.Errorf("delete lifecycle broker %T: %w", obj, err)
			}
		}
		return nil
	}

	sa := &corev1.ServiceAccount{ObjectMeta: meta}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, sa, func(client.Object) error { return nil }); err != nil {
		return fmt.Errorf("upsert lifecycle broker ServiceAccount: %w", err)
	}
	role := &rbacv1.Role{ObjectMeta: meta, Rules: LifecycleBrokerRules(collective)}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, role, func(existing client.Object) error {
		existing.(*rbacv1.Role).Rules = role.Rules
		return nil
	}); err != nil {
		return fmt.Errorf("upsert lifecycle broker Role: %w", err)
	}
	binding := &rbacv1.RoleBinding{
		ObjectMeta: meta,
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "Role", Name: name},
		Subjects:   []rbacv1.Subject{{Kind: rbacv1.ServiceAccountKind, Name: name, Namespace: ns}},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, binding, func(existing client.Object) error {
		existing.(*rbacv1.RoleBinding).Subjects = binding.Subjects
		return nil
	}); err != nil {
		return fmt.Errorf("upsert lifecycle broker RoleBinding: %w", err)
	}
	deploy := LifecycleBrokerDeployment(corpus, collective)
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, deploy, func(existing client.Object) error {
		d := existing.(*appsv1.Deployment)
		d.Labels = deploy.Labels
		d.Spec.Replicas = deploy.Spec.Replicas
		d.Spec.Template = deploy.Spec.Template
		return nil
	}); err != nil {
		return fmt.Errorf("upsert lifecycle broker Deployment: %w", err)
	}
	return nil
}
