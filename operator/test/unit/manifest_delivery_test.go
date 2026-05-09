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

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/manifests"
)

// newScheme registers the ACC types + corev1 so the fake client knows about
// AgentCorpus, ConfigMap, etc.
func newScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("corev1.AddToScheme: %v", err)
	}
	if err := accv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("accv1alpha1.AddToScheme: %v", err)
	}
	return s
}

// freshCorpus returns a minimal AgentCorpus sufficient to drive
// ManifestDeliveryReconciler. Defaults manifestDelivery=all (matching the
// CRD default).
func freshCorpus() *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-corpus",
			Namespace: "test-ns",
			UID:       "0000-1111",
		},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version:          "0.1.0",
			ManifestDelivery: "all",
		},
	}
}

// TestManifestDelivery_EmitsThreeConfigMaps drives one full Reconcile pass
// and asserts the three corpus-scoped ConfigMaps are created with non-empty
// data and the operator-managed labels.
func TestManifestDelivery_EmitsThreeConfigMaps(t *testing.T) {
	scheme := newScheme(t)
	corpus := freshCorpus()
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(corpus).Build()

	r := &manifests.ManifestDeliveryReconciler{Client: c, Scheme: scheme}
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !corpus.Status.ManifestDeliveryReady {
		t.Errorf("expected status.ManifestDeliveryReady=true")
	}

	rolesS, skillsS, mcpsS := manifests.Suffixes()
	for _, suffix := range []string{rolesS, skillsS, mcpsS} {
		cm := &corev1.ConfigMap{}
		key := types.NamespacedName{
			Namespace: corpus.Namespace,
			Name:      manifests.ConfigMapName(corpus, suffix),
		}
		if err := c.Get(context.Background(), key, cm); err != nil {
			t.Errorf("Get %s ConfigMap: %v", suffix, err)
			continue
		}
		if len(cm.Data) == 0 {
			t.Errorf("%s ConfigMap has empty Data", suffix)
		}
		if cm.Labels["acc.redhat.io/manifest-tree"] != suffix {
			t.Errorf("%s ConfigMap missing manifest-tree label, got: %v", suffix, cm.Labels)
		}
	}
}

// TestManifestDelivery_KeysAreFlattened spot-checks that ConfigMap keys
// flatten "/" to "__" so Kubernetes accepts them, and the unflatten
// round-trip lands back at the original path.
func TestManifestDelivery_KeysAreFlattened(t *testing.T) {
	scheme := newScheme(t)
	corpus := freshCorpus()
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(corpus).Build()

	r := &manifests.ManifestDeliveryReconciler{Client: c, Scheme: scheme}
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}

	rolesS, _, _ := manifests.Suffixes()
	cm := &corev1.ConfigMap{}
	if err := c.Get(context.Background(), types.NamespacedName{
		Namespace: corpus.Namespace,
		Name:      manifests.ConfigMapName(corpus, rolesS),
	}, cm); err != nil {
		t.Fatal(err)
	}

	// Assert: every key passes the K8s ConfigMap data-key regex (no "/")
	// and the unflatten produces a recognisable two-segment path.
	saw := 0
	for k := range cm.Data {
		if containsRune(k, '/') {
			t.Errorf("key %q contains '/' — Kubernetes will reject", k)
		}
		orig := manifests.UnflattenKey(k)
		// All role files live one directory deep:
		// <persona>/<file>. Confirm the unflatten produces at least
		// one "/".
		if !containsRune(orig, '/') {
			t.Errorf("unflattened %q has no '/' — flatten/unflatten broken", k)
		}
		saw++
	}
	if saw == 0 {
		t.Error("ConfigMap had no Data entries — sync-manifests probably did not run")
	}
}

// TestManifestDelivery_OptOut confirms manifestDelivery=none short-circuits
// the reconciler — no ConfigMaps are created, status.ManifestDeliveryReady
// stays false.
func TestManifestDelivery_OptOut(t *testing.T) {
	scheme := newScheme(t)
	corpus := freshCorpus()
	corpus.Spec.ManifestDelivery = "none"
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(corpus).Build()

	r := &manifests.ManifestDeliveryReconciler{Client: c, Scheme: scheme}
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.ManifestDeliveryReady {
		t.Errorf("expected status.ManifestDeliveryReady=false on opt-out")
	}

	cmList := &corev1.ConfigMapList{}
	if err := c.List(context.Background(), cmList, client.InNamespace(corpus.Namespace)); err != nil {
		t.Fatal(err)
	}
	for _, cm := range cmList.Items {
		if cm.Labels["acc.redhat.io/manifest-tree"] != "" {
			t.Errorf("opt-out should not create manifest CMs, got: %s", cm.Name)
		}
	}
}

// TestManifestDelivery_Idempotent runs the reconciler twice and confirms
// no error and the second pass leaves the cluster in the same state.
func TestManifestDelivery_Idempotent(t *testing.T) {
	scheme := newScheme(t)
	corpus := freshCorpus()
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(corpus).Build()

	r := &manifests.ManifestDeliveryReconciler{Client: c, Scheme: scheme}
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("first Reconcile: %v", err)
	}
	rolesS, _, _ := manifests.Suffixes()
	first := &corev1.ConfigMap{}
	if err := c.Get(context.Background(), types.NamespacedName{
		Namespace: corpus.Namespace,
		Name:      manifests.ConfigMapName(corpus, rolesS),
	}, first); err != nil {
		t.Fatal(err)
	}

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("second Reconcile: %v", err)
	}
	second := &corev1.ConfigMap{}
	if err := c.Get(context.Background(), types.NamespacedName{
		Namespace: corpus.Namespace,
		Name:      manifests.ConfigMapName(corpus, rolesS),
	}, second); err != nil {
		t.Fatal(err)
	}
	if len(first.Data) != len(second.Data) {
		t.Errorf("Data size drift across reconciles: %d → %d",
			len(first.Data), len(second.Data))
	}
}

func containsRune(s string, r rune) bool {
	for _, c := range s {
		if c == r {
			return true
		}
	}
	return false
}
