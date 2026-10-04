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
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// OpenSpec 20261003-assistant-orchestrated-infusion phase 6: spec.lifecycle
// runs a broker whose ServiceAccount may get and patch its own
// AgentCollective and nothing else; turning it off removes the power.

func lifecycleFixture(t *testing.T, enabled bool, nkeys bool) (client.Client, *LifecycleBrokerReconciler, *accv1alpha1.AgentCorpus, *accv1alpha1.AgentCollective) {
	t.Helper()
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		corev1.AddToScheme, appsv1.AddToScheme, rbacv1.AddToScheme, accv1alpha1.AddToScheme,
	} {
		if err := add(s); err != nil {
			t.Fatalf("AddToScheme: %v", err)
		}
	}
	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "acc", Namespace: "acc-proj", UID: "corpus-uid"},
		Spec:       accv1alpha1.AgentCorpusSpec{Version: "0.26.4"},
	}
	if nkeys {
		corpus.Spec.Infrastructure.NATS.NKeyAuth = &accv1alpha1.NKeyAuthSpec{Enabled: true}
	}
	coll := &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "sol-01", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID: "sol-01",
			Lifecycle: &accv1alpha1.LifecycleSpec{
				Enabled:     enabled,
				MaxReplicas: 4,
				VerifyKey: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "arbiter-keys"},
					Key:                  "verify",
				},
			},
		},
	}
	cl := fake.NewClientBuilder().WithScheme(s).Build()
	return cl, &LifecycleBrokerReconciler{Client: cl, Scheme: s}, corpus, coll
}

func brokerKey() types.NamespacedName {
	return types.NamespacedName{Namespace: "acc-proj", Name: "sol-01-lifecycle-broker"}
}

func TestLifecycleBroker_DisabledCreatesNothing(t *testing.T) {
	cl, r, corpus, coll := lifecycleFixture(t, false, false)
	if err := r.ReconcileCollective(context.Background(), corpus, coll); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if err := cl.Get(context.Background(), brokerKey(), &appsv1.Deployment{}); !apierrors.IsNotFound(err) {
		t.Fatalf("broker Deployment should not exist, got err=%v", err)
	}
}

func TestLifecycleBroker_RoleIsThisCollectiveOnly(t *testing.T) {
	cl, r, corpus, coll := lifecycleFixture(t, true, false)
	if err := r.ReconcileCollective(context.Background(), corpus, coll); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	role := &rbacv1.Role{}
	if err := cl.Get(context.Background(), brokerKey(), role); err != nil {
		t.Fatalf("Role not created: %v", err)
	}
	if len(role.Rules) != 1 {
		t.Fatalf("want exactly one rule, got %d", len(role.Rules))
	}
	rule := role.Rules[0]
	if len(rule.ResourceNames) != 1 || rule.ResourceNames[0] != "sol-01" {
		t.Errorf("rule must be resourceNames-scoped to sol-01, got %v", rule.ResourceNames)
	}
	if len(rule.Resources) != 1 || rule.Resources[0] != "agentcollectives" {
		t.Errorf("rule must cover agentcollectives only, got %v", rule.Resources)
	}
	for _, v := range rule.Verbs {
		if v != "get" && v != "patch" {
			t.Errorf("unexpected verb %q (want get, patch)", v)
		}
	}
	rb := &rbacv1.RoleBinding{}
	if err := cl.Get(context.Background(), brokerKey(), rb); err != nil {
		t.Fatalf("RoleBinding not created: %v", err)
	}
	if rb.Subjects[0].Name != "sol-01-lifecycle-broker" || rb.RoleRef.Name != "sol-01-lifecycle-broker" {
		t.Errorf("binding wires the wrong subject/role: %+v %+v", rb.Subjects, rb.RoleRef)
	}
}

func TestLifecycleBroker_DeploymentRunsTheKubernetesRuntime(t *testing.T) {
	cl, r, corpus, coll := lifecycleFixture(t, true, true)
	if err := r.ReconcileCollective(context.Background(), corpus, coll); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	d := &appsv1.Deployment{}
	if err := cl.Get(context.Background(), brokerKey(), d); err != nil {
		t.Fatalf("Deployment not created: %v", err)
	}
	pod := d.Spec.Template.Spec
	if pod.ServiceAccountName != "sol-01-lifecycle-broker" {
		t.Errorf("pod must run as the broker ServiceAccount, got %q", pod.ServiceAccountName)
	}
	c := pod.Containers[0]
	want := map[string]string{
		"ACC_LIFECYCLE_RUNTIME":      "kubernetes",
		"ACC_COLLECTIVE_CR_NAME":     "sol-01",
		"ACC_COLLECTIVE_ID":          "sol-01",
		"ACC_LIFECYCLE_MAX_REPLICAS": "4",
		"ACC_NKEY_ROLE":              "lifecycle_broker",
	}
	for k, v := range want {
		if got, ok := envValue(c, k); !ok || got != v {
			t.Errorf("env %s = %q (present=%v), want %q", k, got, ok, v)
		}
	}
	var verify *corev1.EnvVar
	for i := range c.Env {
		if c.Env[i].Name == "ACC_ARBITER_VERIFY_KEY" {
			verify = &c.Env[i]
		}
	}
	if verify == nil || verify.ValueFrom == nil || verify.ValueFrom.SecretKeyRef == nil ||
		verify.ValueFrom.SecretKeyRef.Name != "arbiter-keys" || verify.ValueFrom.SecretKeyRef.Key != "verify" {
		t.Errorf("verify key must come from the named Secret key, got %+v", verify)
	}
	// Only the broker's own seed is projected.
	var items []corev1.KeyToPath
	for _, v := range pod.Volumes {
		if v.Secret != nil && v.Secret.SecretName == "acc-nats-nkeys" {
			items = v.Secret.Items
		}
	}
	if len(items) != 1 || items[0].Key != "seed-lifecycle_broker" {
		t.Errorf("want only seed-lifecycle_broker projected, got %+v", items)
	}
}

func TestLifecycleBroker_DisablingRemovesThePower(t *testing.T) {
	cl, r, corpus, coll := lifecycleFixture(t, true, false)
	ctx := context.Background()
	if err := r.ReconcileCollective(ctx, corpus, coll); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	coll.Spec.Lifecycle.Enabled = false
	if err := r.ReconcileCollective(ctx, corpus, coll); err != nil {
		t.Fatalf("reconcile (disabled): %v", err)
	}
	for _, obj := range []client.Object{
		&appsv1.Deployment{}, &rbacv1.Role{}, &rbacv1.RoleBinding{}, &corev1.ServiceAccount{},
	} {
		if err := cl.Get(ctx, brokerKey(), obj); !apierrors.IsNotFound(err) {
			t.Errorf("%T should be gone after disabling, err=%v", obj, err)
		}
	}
}
