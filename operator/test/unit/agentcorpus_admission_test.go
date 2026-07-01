// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Admission tests for the AgentCorpus webhook covering the two fixes tracked in
// lab-gitops backlog 010:
//   - G1: the defaulter must backfill observability.otelCollector.endpoint when
//     it is EMPTY (what the OpenShift "Create" form submits), not only when the
//     whole block is nil.
//   - G2: milvus.uri is required only when vectorBackend=milvus — an rhoai corpus
//     on the default TurboVec backend must validate without it.
package unit_test

import (
	"context"
	"strings"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// validBaseCorpus returns a corpus that passes every webhook validation EXCEPT
// the rule under test: wasmConfigMapRef set (G3), an otel endpoint set, and one
// collective. Callers tweak the field they are exercising.
func validBaseCorpus(name string) *accv1alpha1.AgentCorpus {
	c := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "acc-system"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version:     "0.5.28",
			Collectives: []accv1alpha1.CollectiveRef{{Name: "c1"}},
		},
	}
	c.Spec.Governance.CategoryA.WASMConfigMapRef = "acc-cat-a-wasm"
	c.Spec.Observability.Backend = accv1alpha1.MetricsBackendOTel
	c.Spec.Observability.OTelCollector = &accv1alpha1.OTelCollectorSpec{Endpoint: "x:4317"}
	return c
}

// G2: an rhoai corpus on TurboVec (explicit or default) validates with no Milvus.
func TestValidate_RHOAITurboVecNoMilvus(t *testing.T) {
	c := validBaseCorpus("corpus")
	c.Spec.DeployMode = accv1alpha1.DeployModeRHOAI

	c.Spec.Infrastructure.VectorBackend = "turbovec"
	if _, err := c.ValidateCreate(); err != nil {
		t.Fatalf("rhoai+turbovec must validate without milvus.uri, got: %v", err)
	}

	// Empty vectorBackend means "operator default" (turbovec on rhoai) — also OK.
	c.Spec.Infrastructure.VectorBackend = ""
	if _, err := c.ValidateCreate(); err != nil {
		t.Fatalf("rhoai+default backend must validate without milvus.uri, got: %v", err)
	}
}

// G2: when Milvus IS the selected backend, milvus.uri remains required.
func TestValidate_MilvusBackendRequiresURI(t *testing.T) {
	c := validBaseCorpus("corpus")
	c.Spec.DeployMode = accv1alpha1.DeployModeRHOAI
	c.Spec.Infrastructure.VectorBackend = "milvus"

	_, err := c.ValidateCreate()
	if err == nil {
		t.Fatal("milvus.uri must be required when vectorBackend=milvus, got nil error")
	}
	if !strings.Contains(err.Error(), "milvus.uri") {
		t.Errorf("expected a milvus.uri error, got: %v", err)
	}

	c.Spec.Infrastructure.Milvus = &accv1alpha1.MilvusSpec{URI: "milvus.acc:19530"}
	if _, err := c.ValidateCreate(); err != nil {
		t.Fatalf("milvus backend with a URI must validate, got: %v", err)
	}
}

// G1: a present-but-empty otelCollector block (the OpenShift form's submission)
// gets its endpoint backfilled by the defaulter to the in-cluster Collector.
func TestDefault_BackfillsEmptyOTelEndpoint(t *testing.T) {
	d := &accv1alpha1.AgentCorpusCustomDefaulter{Client: kserveClient(t)}
	c := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "corpus", Namespace: "acc-system"},
		Spec: accv1alpha1.AgentCorpusSpec{
			DeployMode: accv1alpha1.DeployModeStandalone,
			Observability: accv1alpha1.ObservabilitySpec{
				Backend:       accv1alpha1.MetricsBackendOTel,
				OTelCollector: &accv1alpha1.OTelCollectorSpec{Endpoint: ""},
			},
		},
	}
	if err := d.Default(context.Background(), c); err != nil {
		t.Fatalf("Default: %v", err)
	}
	if got := c.Spec.Observability.OTelCollector.Endpoint; got != "corpus-otel-collector:4317" {
		t.Errorf("expected backfilled endpoint corpus-otel-collector:4317, got %q", got)
	}
}

// G1 regression: a nil otelCollector block is still materialized + filled.
func TestDefault_BackfillsNilOTelBlock(t *testing.T) {
	d := &accv1alpha1.AgentCorpusCustomDefaulter{Client: kserveClient(t)}
	c := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "corpus", Namespace: "acc-system"},
		Spec: accv1alpha1.AgentCorpusSpec{
			DeployMode: accv1alpha1.DeployModeStandalone,
			Observability: accv1alpha1.ObservabilitySpec{
				Backend: accv1alpha1.MetricsBackendOTel,
			},
		},
	}
	if err := d.Default(context.Background(), c); err != nil {
		t.Fatalf("Default: %v", err)
	}
	if c.Spec.Observability.OTelCollector == nil {
		t.Fatal("expected OTelCollector materialized")
	}
	if got := c.Spec.Observability.OTelCollector.Endpoint; got != "corpus-otel-collector:4317" {
		t.Errorf("expected endpoint corpus-otel-collector:4317, got %q", got)
	}
}
