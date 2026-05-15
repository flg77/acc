// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the SpiffeReconciler — proposal 011 PR-2.
package unit_test

import (
	"context"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

var clusterSPIFFEIDGVK = schema.GroupVersionKind{
	Group:   "spire.spiffe.io",
	Version: "v1alpha1",
	Kind:    "ClusterSPIFFEID",
}

// spiffeCorpus returns a minimal corpus with the SPIRE prerequisite
// flag controllable.
func spiffeCorpus(spireInstalled bool) *accv1alpha1.AgentCorpus {
	c := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-corpus",
			Namespace: "test-ns",
		},
		Spec: accv1alpha1.AgentCorpusSpec{Version: "0.1.0"},
	}
	c.Status.Prerequisites.SpireInstalled = spireInstalled
	return c
}

// spiffeCollective returns a minimal collective with a controllable
// SpiffeSpec.
func spiffeCollective(spiffe *accv1alpha1.SpiffeSpec) *accv1alpha1.AgentCollective {
	return &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "research",
			Namespace: "test-ns",
		},
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID: "research-01",
			Spiffe:       spiffe,
		},
	}
}

// spiffeClient builds a fake client that knows the ACC scheme and can
// track the unstructured ClusterSPIFFEID CR.  The spire-controller-
// manager CRD is registered as an unstructured type so the fake
// client's object tracker can map its GVK.
func spiffeClient(t *testing.T, objs ...client.Object) client.Client {
	t.Helper()
	s := newScheme(t)
	s.AddKnownTypeWithName(clusterSPIFFEIDGVK, &unstructured.Unstructured{})
	listGVK := clusterSPIFFEIDGVK
	listGVK.Kind = "ClusterSPIFFEIDList"
	s.AddKnownTypeWithName(listGVK, &unstructured.UnstructuredList{})
	return fake.NewClientBuilder().
		WithScheme(s).
		WithObjects(objs...).
		WithStatusSubresource(&accv1alpha1.AgentCollective{}).
		Build()
}


func TestSpiffeReconciler_DisabledIsNoop(t *testing.T) {
	r := &collective.SpiffeReconciler{Client: spiffeClient(t), Scheme: newScheme(t)}
	corpus := spiffeCorpus(true)
	// spiffe == nil
	col := spiffeCollective(nil)
	res, err := r.ReconcileCollective(context.Background(), corpus, col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if res.Issued || res.SpiffeID != "" || res.Err != "" {
		t.Errorf("disabled spiffe should yield empty result, got %+v", res)
	}
}

func TestSpiffeReconciler_EnabledFalseIsNoop(t *testing.T) {
	r := &collective.SpiffeReconciler{Client: spiffeClient(t), Scheme: newScheme(t)}
	corpus := spiffeCorpus(true)
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{Enabled: false})
	res, err := r.ReconcileCollective(context.Background(), corpus, col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if res.Issued || res.Err != "" {
		t.Errorf("enabled=false should yield empty result, got %+v", res)
	}
}

func TestSpiffeReconciler_SpireAbsentReportsError(t *testing.T) {
	r := &collective.SpiffeReconciler{Client: spiffeClient(t), Scheme: newScheme(t)}
	corpus := spiffeCorpus(false) // SPIRE NOT installed
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:     true,
		TrustDomain: "acc-prod.example.com",
	})
	res, err := r.ReconcileCollective(context.Background(), corpus, col)
	if err != nil {
		t.Fatalf("ReconcileCollective should not error when SPIRE absent: %v", err)
	}
	if res.Issued {
		t.Error("Issued should be false when SPIRE absent")
	}
	if res.Err == "" {
		t.Error("Err should be set when SPIRE absent")
	}
}

func TestSpiffeReconciler_IssuesClusterSPIFFEID(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	corpus := spiffeCorpus(true)
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:     true,
		TrustDomain: "acc-prod.example.com",
	})

	res, err := r.ReconcileCollective(context.Background(), corpus, col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if !res.Issued {
		t.Errorf("Issued should be true, got %+v", res)
	}
	want := "spiffe://acc-prod.example.com/role/research"
	if res.SpiffeID != want {
		t.Errorf("SpiffeID: got %q want %q", res.SpiffeID, want)
	}

	// The ClusterSPIFFEID CR must now exist.
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(clusterSPIFFEIDGVK)
	if err := cl.Get(context.Background(), types.NamespacedName{
		Name: "acc-test-ns-research",
	}, u); err != nil {
		t.Fatalf("ClusterSPIFFEID not created: %v", err)
	}
	tmpl, _, _ := unstructured.NestedString(u.Object, "spec", "spiffeIDTemplate")
	if tmpl != want {
		t.Errorf("spiffeIDTemplate: got %q want %q", tmpl, want)
	}
	sel, _, _ := unstructured.NestedString(
		u.Object, "spec", "podSelector", "matchLabels", "acc.io/collective",
	)
	if sel != "research-01" {
		t.Errorf("podSelector label: got %q want research-01", sel)
	}
}

func TestSpiffeReconciler_DerivesTrustDomainWhenBlank(t *testing.T) {
	r := &collective.SpiffeReconciler{Client: spiffeClient(t), Scheme: newScheme(t)}
	corpus := spiffeCorpus(true)
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled: true,
		// TrustDomain intentionally blank → derive <corpus>.acc.local
	})
	res, err := r.ReconcileCollective(context.Background(), corpus, col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	want := "spiffe://test-corpus.acc.local/role/research"
	if res.SpiffeID != want {
		t.Errorf("derived SpiffeID: got %q want %q", res.SpiffeID, want)
	}
}

func TestSpiffeReconciler_IdempotentSecondCall(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	corpus := spiffeCorpus(true)
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:     true,
		TrustDomain: "acc-prod.example.com",
	})
	ctx := context.Background()

	res1, err := r.ReconcileCollective(ctx, corpus, col)
	if err != nil {
		t.Fatalf("first ReconcileCollective: %v", err)
	}
	res2, err := r.ReconcileCollective(ctx, corpus, col)
	if err != nil {
		t.Fatalf("second ReconcileCollective: %v", err)
	}
	if res1.SpiffeID != res2.SpiffeID || !res2.Issued {
		t.Errorf("second call not idempotent: %+v vs %+v", res1, res2)
	}
}

func TestSpiffeReconciler_DeleteClusterSPIFFEID(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	corpus := spiffeCorpus(true)
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:     true,
		TrustDomain: "acc-prod.example.com",
	})
	ctx := context.Background()

	if _, err := r.ReconcileCollective(ctx, corpus, col); err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	// Delete should succeed + be idempotent.
	if err := r.DeleteClusterSPIFFEID(ctx, corpus, col.Name); err != nil {
		t.Fatalf("DeleteClusterSPIFFEID: %v", err)
	}
	if err := r.DeleteClusterSPIFFEID(ctx, corpus, col.Name); err != nil {
		t.Fatalf("second DeleteClusterSPIFFEID (not-found) should be a no-op: %v", err)
	}
}
