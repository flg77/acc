// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the edge SPIFFE topology — proposal 012 PR-2.
// Exercises SpiffeReconciler's site-qualified SPIFFE ID computation.
package unit_test

import (
	"context"
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

// edgeCorpus returns a SPIRE-installed corpus with deployMode=edge.
func edgeCorpus() *accv1alpha1.AgentCorpus {
	c := spiffeCorpus(true)
	c.Spec.DeployMode = accv1alpha1.DeployModeEdge
	return c
}

func TestSpiffeEdge_NestedProducesSiteQualifiedID(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "acc-prod.example.com",
		EdgeTopology: "nested",
		EdgeSiteID:   "factory-a",
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	want := "spiffe://acc-prod.example.com/edge/factory-a/role/research"
	if res.SpiffeID != want {
		t.Errorf("SpiffeID: got %q want %q", res.SpiffeID, want)
	}
	if res.EdgeSiteID != "factory-a" {
		t.Errorf("EdgeSiteID: got %q want factory-a", res.EdgeSiteID)
	}
	if !res.Issued {
		t.Error("Issued should be true")
	}
}

func TestSpiffeEdge_NestedWithoutSiteIDReportsError(t *testing.T) {
	r := &collective.SpiffeReconciler{Client: spiffeClient(t), Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "acc-prod.example.com",
		EdgeTopology: "nested",
		// EdgeSiteID intentionally blank
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective should not hard-error: %v", err)
	}
	if res.Issued {
		t.Error("Issued should be false when edgeSiteID missing")
	}
	if res.Err == "" {
		t.Error("Err should be set when nested topology lacks edgeSiteID")
	}
}

func TestSpiffeEdge_FederatedUsesPlainID(t *testing.T) {
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "factory-b.acc.local",
		EdgeTopology: "federated",
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	// Federated: the trust domain IS the scope — no /edge/<site> segment.
	want := "spiffe://factory-b.acc.local/role/research"
	if res.SpiffeID != want {
		t.Errorf("SpiffeID: got %q want %q", res.SpiffeID, want)
	}
	if res.EdgeSiteID != "" {
		t.Errorf("EdgeSiteID should be empty for federated, got %q", res.EdgeSiteID)
	}
}

func TestSpiffeEdge_NonEdgeIgnoresTopology(t *testing.T) {
	// A rhoai corpus with edgeTopology=nested set on the spec must NOT
	// produce a site-qualified ID — the edge fields are edge-only.
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	corpus := spiffeCorpus(true) // deployMode unset → not edge
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "acc-prod.example.com",
		EdgeTopology: "nested",
		EdgeSiteID:   "ignored",
	})
	res, err := r.ReconcileCollective(context.Background(), corpus, col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	want := "spiffe://acc-prod.example.com/role/research"
	if res.SpiffeID != want {
		t.Errorf("non-edge SpiffeID: got %q want %q", res.SpiffeID, want)
	}
	if res.EdgeSiteID != "" {
		t.Errorf("non-edge EdgeSiteID should be empty, got %q", res.EdgeSiteID)
	}
}

func TestSpiffeEdge_Ed25519TopologyUsesPlainID(t *testing.T) {
	// edgeTopology=ed25519 still produces a plain SPIFFE ID when the
	// operator nonetheless enabled spiffe — the topology only governs
	// the path shape, not whether SPIFFE runs.
	cl := spiffeClient(t)
	r := &collective.SpiffeReconciler{Client: cl, Scheme: newScheme(t)}
	col := spiffeCollective(&accv1alpha1.SpiffeSpec{
		Enabled:      true,
		TrustDomain:  "acc-prod.example.com",
		EdgeTopology: "ed25519",
	})
	res, err := r.ReconcileCollective(context.Background(), edgeCorpus(), col)
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	want := "spiffe://acc-prod.example.com/role/research"
	if res.SpiffeID != want {
		t.Errorf("SpiffeID: got %q want %q", res.SpiffeID, want)
	}
}
