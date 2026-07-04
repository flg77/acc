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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/sandbox"
)

// reconcileSandboxWorkload is the OpenShell Phase-3 attach: it must upsert the
// agent AS an Agent Sandbox `Sandbox` CR (owned by the collective) — NOT a
// StatefulSet.
func TestReconcileSandboxWorkload_CreatesSandboxCR(t *testing.T) {
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		corev1.AddToScheme, appsv1.AddToScheme, accv1alpha1.AddToScheme,
	} {
		if err := add(s); err != nil {
			t.Fatalf("AddToScheme: %v", err)
		}
	}
	// The Agent Sandbox CR is emitted as unstructured; register its GVK (+ list
	// kind) so the fake client's tracker can create/get it.
	s.AddKnownTypeWithName(sandbox.SandboxGVK, &unstructured.Unstructured{})
	s.AddKnownTypeWithName(
		sandbox.SandboxGVK.GroupVersion().WithKind("SandboxList"), &unstructured.UnstructuredList{})

	cl := fake.NewClientBuilder().WithScheme(s).Build()
	r := &AgentDeploymentReconciler{Client: cl, Scheme: s}

	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version: "0.1.0",
			Sandbox: &accv1alpha1.SandboxSpec{Enabled: ptr.To(true), GatewayURL: "https://gw:8080"},
		},
	}
	coll := &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "acc-proj"},
	}
	podTemplate := corev1.PodTemplateSpec{
		Spec: corev1.PodSpec{Containers: []corev1.Container{{Name: "agent", Image: "acc/agent:latest"}}},
	}

	ready, desired, progressing, err := r.reconcileSandboxWorkload(
		context.Background(), corpus, coll, "demo-coding", "acc-proj", podTemplate)
	if err != nil {
		t.Fatalf("reconcileSandboxWorkload: %v", err)
	}
	if ready != 0 || desired != 1 || !progressing {
		t.Errorf("status = ready %d / desired %d / progressing %v, want 0 / 1 / true", ready, desired, progressing)
	}

	// The Sandbox CR exists at the deployment name, owned by the collective.
	got := &unstructured.Unstructured{}
	got.SetGroupVersionKind(sandbox.SandboxGVK)
	if err := cl.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: "demo-coding"}, got); err != nil {
		t.Fatalf("Sandbox CR not created: %v", err)
	}
	if _, found, _ := unstructured.NestedMap(got.Object, "spec", "podTemplate"); !found {
		t.Error("Sandbox spec.podTemplate missing")
	}
	if refs := got.GetOwnerReferences(); len(refs) != 1 || refs[0].Name != "demo-set" {
		t.Errorf("ownerReferences = %v, want single owner demo-set", got.GetOwnerReferences())
	}

	// Idempotent: a second reconcile upserts cleanly (no create-conflict).
	if _, _, _, err := r.reconcileSandboxWorkload(
		context.Background(), corpus, coll, "demo-coding", "acc-proj", podTemplate); err != nil {
		t.Fatalf("second (idempotent) reconcile failed: %v", err)
	}
}
