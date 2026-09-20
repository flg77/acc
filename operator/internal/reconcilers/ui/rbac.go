// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const uiRBACComponent = "ui"

// UIServiceAccountName is the ServiceAccount the corpus's TUI and WebGUI pods
// run as.
func UIServiceAccountName(corpus *accv1alpha1.AgentCorpus) string {
	return fmt.Sprintf("%s-ui", corpus.Name)
}

// UIReaderRules is everything a UI pod may ask the cluster: the four ACC
// kinds of its own namespace, read-only. The surfaces show the deployment
// they belong to (the Agentset screen is the AgentCollective, packages are
// AccPackageInstalls) — on the namespace's `default` ServiceAccount they
// could read none of it and sent the operator to `oc` instead.
//
// Deliberately absent: every write verb, every core resource (no pods, no
// secrets, no configmaps), every other API group. What the agents are doing
// right now reaches the surfaces over the bus, not from here.
func UIReaderRules() []rbacv1.PolicyRule {
	return []rbacv1.PolicyRule{{
		APIGroups: []string{accv1alpha1.GroupVersion.Group},
		Resources: []string{"agentcorpora", "agentcollectives", "accpackageinstalls", "acccatalogs"},
		Verbs:     []string{"get", "list", "watch"},
	}}
}

// withUIServiceAccount upserts the corpus's UI ServiceAccount with its
// read-only Role and RoleBinding, and makes podSpec run as it.
func withUIServiceAccount(ctx context.Context, c client.Client, scheme *runtime.Scheme, corpus *accv1alpha1.AgentCorpus, podSpec *corev1.PodSpec) error {
	name := UIServiceAccountName(corpus)
	labels := util.CommonLabels(corpus.Name, uiRBACComponent, corpus.Spec.Version)
	meta := metav1.ObjectMeta{Name: name, Namespace: corpus.Namespace, Labels: labels}

	sa := &corev1.ServiceAccount{ObjectMeta: meta}
	if _, err := util.Upsert(ctx, c, scheme, corpus, sa, func(client.Object) error { return nil }); err != nil {
		return fmt.Errorf("upsert ui ServiceAccount: %w", err)
	}

	role := &rbacv1.Role{ObjectMeta: meta, Rules: UIReaderRules()}
	if _, err := util.Upsert(ctx, c, scheme, corpus, role, func(existing client.Object) error {
		existing.(*rbacv1.Role).Rules = role.Rules
		return nil
	}); err != nil {
		return fmt.Errorf("upsert ui Role: %w", err)
	}

	binding := &rbacv1.RoleBinding{
		ObjectMeta: meta,
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "Role", Name: name},
		Subjects:   []rbacv1.Subject{{Kind: rbacv1.ServiceAccountKind, Name: name, Namespace: corpus.Namespace}},
	}
	if _, err := util.Upsert(ctx, c, scheme, corpus, binding, func(existing client.Object) error {
		// RoleRef is immutable; the subjects are the only thing that can drift.
		existing.(*rbacv1.RoleBinding).Subjects = binding.Subjects
		return nil
	}); err != nil {
		return fmt.Errorf("upsert ui RoleBinding: %w", err)
	}

	podSpec.ServiceAccountName = name
	return nil
}
