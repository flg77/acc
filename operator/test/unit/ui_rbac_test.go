// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
)

// The UI pods show the deployment they belong to (the Agentset screen IS the
// AgentCollective). On the namespace's `default` ServiceAccount they could
// read none of it; they run as <corpus>-ui, which may read the four ACC kinds
// of its own namespace and nothing else.
func TestUIRBAC_ReaderRulesAreReadOnlyAndACCOnly(t *testing.T) {
	rules := ui.UIReaderRules()
	if len(rules) != 1 {
		t.Fatalf("want one rule, got %d", len(rules))
	}
	r := rules[0]
	if len(r.APIGroups) != 1 || r.APIGroups[0] != "acc.redhat.io" {
		t.Errorf("apiGroups = %v, want only acc.redhat.io (no core resources: no pods, no secrets)", r.APIGroups)
	}
	for _, v := range r.Verbs {
		if v != "get" && v != "list" && v != "watch" {
			t.Errorf("verb %q is not read-only", v)
		}
	}
	want := map[string]bool{"agentcorpora": true, "agentcollectives": true, "accpackageinstalls": true, "acccatalogs": true}
	if len(r.Resources) != len(want) {
		t.Errorf("resources = %v, want exactly the four ACC kinds", r.Resources)
	}
	for _, res := range r.Resources {
		if !want[res] {
			t.Errorf("unexpected resource %q", res)
		}
	}
}

func TestUIRBAC_TUIRunsAsTheUIServiceAccount(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true)})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	ctx := context.Background()
	key := types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-ui"}

	sa := &corev1.ServiceAccount{}
	if err := c.Get(ctx, key, sa); err != nil {
		t.Fatalf("expected ServiceAccount %s: %v", key.Name, err)
	}
	if len(sa.OwnerReferences) != 1 || sa.OwnerReferences[0].Kind != "AgentCorpus" {
		t.Errorf("the ServiceAccount must be owned by the corpus (garbage-collected with it), got %v", sa.OwnerReferences)
	}
	role := &rbacv1.Role{}
	if err := c.Get(ctx, key, role); err != nil {
		t.Fatalf("expected Role %s: %v", key.Name, err)
	}
	if len(role.Rules) != 1 || role.Rules[0].APIGroups[0] != "acc.redhat.io" {
		t.Errorf("Role rules = %v", role.Rules)
	}
	rb := &rbacv1.RoleBinding{}
	if err := c.Get(ctx, key, rb); err != nil {
		t.Fatalf("expected RoleBinding %s: %v", key.Name, err)
	}
	if rb.RoleRef.Kind != "Role" || rb.RoleRef.Name != key.Name {
		t.Errorf("RoleBinding roleRef = %v, want the namespaced Role (never a ClusterRole)", rb.RoleRef)
	}
	if len(rb.Subjects) != 1 || rb.Subjects[0].Name != key.Name || rb.Subjects[0].Namespace != "acc-system" {
		t.Errorf("RoleBinding subjects = %v", rb.Subjects)
	}

	deploy := &appsv1.Deployment{}
	if err := c.Get(ctx, types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-tui"}, deploy); err != nil {
		t.Fatalf("expected tui Deployment: %v", err)
	}
	if got := deploy.Spec.Template.Spec.ServiceAccountName; got != key.Name {
		t.Errorf("tui serviceAccountName = %q, want %q", got, key.Name)
	}
}

func TestUIRBAC_WebGUIRunsAsTheUIServiceAccount(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(&accv1alpha1.WebGUISpec{Enabled: ptr.To(true), Keycloak: fullKeycloak()})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, deploy); err != nil {
		t.Fatalf("expected webgui Deployment: %v", err)
	}
	if got := deploy.Spec.Template.Spec.ServiceAccountName; got != "rhoai-corpus-ui" {
		t.Errorf("webgui serviceAccountName = %q, want rhoai-corpus-ui", got)
	}
	// A second pass changes nothing and does not fail on the immutable roleRef.
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("second Reconcile: %v", err)
	}
}
