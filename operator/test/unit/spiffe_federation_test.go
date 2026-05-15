// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the edge federation topology — proposal 012 PR-3.
package unit_test

import (
	"context"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

var clusterFederatedTDGVK = schema.GroupVersionKind{
	Group:   "spire.spiffe.io",
	Version: "v1alpha1",
	Kind:    "ClusterFederatedTrustDomain",
}

// getFederatedTD fetches a ClusterFederatedTrustDomain by name.
func getFederatedTD(t *testing.T, cl client.Client, name string) *unstructured.Unstructured {
	t.Helper()
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(clusterFederatedTDGVK)
	if err := cl.Get(context.Background(), types.NamespacedName{Name: name}, u); err != nil {
		t.Fatalf("ClusterFederatedTrustDomain %q not found: %v", name, err)
	}
	return u
}

func TestSpiffeFederation_IssuesTrustDomainPerPeer(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "factory-a.acc.local",
		EdgeTopology: "federated",
		FederationPeers: []string{
			"factory-b.acc.local@https://factory-b.example.com:8443/bundle",
			"factory-c.acc.local@https://factory-c.example.com:8443/bundle",
		},
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if res.Err != "" {
		t.Fatalf("unexpected Err: %s", res.Err)
	}

	// One ClusterFederatedTrustDomain per peer.
	b := getFederatedTD(t, cl, "acc-test-ns-research-fed-factory-b-acc-local")
	td, _, _ := unstructured.NestedString(b.Object, "spec", "trustDomain")
	if td != "factory-b.acc.local" {
		t.Errorf("peer-b trustDomain: got %q", td)
	}
	url, _, _ := unstructured.NestedString(b.Object, "spec", "bundleEndpointURL")
	if url != "https://factory-b.example.com:8443/bundle" {
		t.Errorf("peer-b bundleEndpointURL: got %q", url)
	}
	profile, _, _ := unstructured.NestedString(
		b.Object, "spec", "bundleEndpointProfile", "type")
	if profile != "https_web" {
		t.Errorf("peer-b profile: got %q want https_web", profile)
	}
	// Peer C also created.
	getFederatedTD(t, cl, "acc-test-ns-research-fed-factory-c-acc-local")
}

func TestSpiffeFederation_NoPeersReportsError(t *testing.T) {
	r := &collective.SpiffeReconciler{Client: spiffeClient(t), Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "factory-a.acc.local",
		EdgeTopology: "federated",
		// FederationPeers empty
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective should not hard-error: %v", err)
	}
	if res.Err == "" {
		t.Error("Err should be set when federated topology has no peers")
	}
}

func TestSpiffeFederation_MalformedPeerReported(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "factory-a.acc.local",
		EdgeTopology: "federated",
		FederationPeers: []string{
			"factory-b.acc.local@https://factory-b.example.com/bundle", // ok
			"this-entry-has-no-at-sign",                                // malformed
		},
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if res.Err == "" {
		t.Error("Err should mention the malformed peer")
	}
	// The well-formed peer still got its CR.
	getFederatedTD(t, cl, "acc-test-ns-research-fed-factory-b-acc-local")
}

func TestSpiffeFederation_NestedTopologyIssuesNoFederation(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "acc-prod.example.com",
		EdgeTopology: "nested",
		EdgeSiteID:   "factory-a",
		// FederationPeers set, but nested topology must ignore them.
		FederationPeers: []string{"x.acc.local@https://x/bundle"},
	})
	if _, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col); err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	// No ClusterFederatedTrustDomain should exist.
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(clusterFederatedTDGVK)
	err := cl.Get(context.Background(),
		types.NamespacedName{Name: "acc-test-ns-research-fed-x-acc-local"}, u)
	if err == nil {
		t.Error("nested topology should not create ClusterFederatedTrustDomain")
	}
}
