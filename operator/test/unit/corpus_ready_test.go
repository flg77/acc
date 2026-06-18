// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Regression tests for proposal 032 Finding B.
//
// The operator NEVER set the InfrastructureReady condition: the NATS/Redis
// reconcilers only returned SubResult.Progressing, and the controller read the
// condition via IsConditionTrue — which reads an ABSENT condition as false. So
// ComputeCorpusPhase rule 3 (`if !InfrastructureReady`) always tripped and a
// corpus could never reach Ready. The pure-function phase test (phase_test.go)
// passed InfrastructureReady:true straight in, masking the missing wiring.
//
// infra.ReadyReconciler closes that gap; these tests exercise the wiring —
// including the end-to-end assertion that a quiescent, healthy corpus computes
// to Ready, which is what phase_test.go could not catch.
package unit_test

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/infra"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

const infraReadyNS = "acc-system"

func infraReadyCorpus() *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "corpus", Namespace: infraReadyNS},
	}
}

func infraReadySts(name string, replicas, ready int32) *appsv1.StatefulSet {
	return &appsv1.StatefulSet{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: infraReadyNS},
		Spec:       appsv1.StatefulSetSpec{Replicas: ptr.To(replicas)},
		Status:     appsv1.StatefulSetStatus{ReadyReplicas: ready},
	}
}

// Healthy NATS + Redis => InfrastructureReady True, not progressing.
func TestInfraReady_TrueWhenNatsRedisReady(t *testing.T) {
	c := kserveClient(t, infraReadySts("corpus-nats", 1, 1), infraReadySts("corpus-redis", 1, 1))
	corpus := infraReadyCorpus()

	res, err := (&infra.ReadyReconciler{Client: c}).Reconcile(context.Background(), corpus)
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady) {
		t.Fatalf("expected InfrastructureReady=True, conditions=%+v", corpus.Status.Conditions)
	}
	if res.Progressing {
		t.Error("expected Progressing=false when infra is healthy")
	}
}

// Redis not ready => False + Progressing.
func TestInfraReady_FalseAndProgressingWhenRedisNotReady(t *testing.T) {
	c := kserveClient(t, infraReadySts("corpus-nats", 1, 1), infraReadySts("corpus-redis", 1, 0))
	corpus := infraReadyCorpus()

	res, err := (&infra.ReadyReconciler{Client: c}).Reconcile(context.Background(), corpus)
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !statuspkg.IsConditionFalse(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady) {
		t.Fatalf("expected InfrastructureReady=False, conditions=%+v", corpus.Status.Conditions)
	}
	if !res.Progressing {
		t.Error("expected Progressing=true while infra is coming up")
	}
}

// A not-yet-created StatefulSet (NotFound) counts as not ready.
func TestInfraReady_FalseWhenStatefulSetMissing(t *testing.T) {
	c := kserveClient(t, infraReadySts("corpus-nats", 1, 1)) // no redis seeded
	corpus := infraReadyCorpus()
	if _, err := (&infra.ReadyReconciler{Client: c}).Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !statuspkg.IsConditionFalse(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady) {
		t.Error("expected InfrastructureReady=False when a StatefulSet is missing")
	}
}

// turbovec/lancedb (no external Milvus URI): Milvus must NOT gate readiness.
func TestInfraReady_IgnoresMilvusWhenNoURI(t *testing.T) {
	c := kserveClient(t, infraReadySts("corpus-nats", 1, 1), infraReadySts("corpus-redis", 1, 1))
	corpus := infraReadyCorpus() // no Spec.Infrastructure.Milvus
	if _, err := (&infra.ReadyReconciler{Client: c}).Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady) {
		t.Error("expected InfrastructureReady=True — Milvus must not gate readiness with no URI (turbovec default)")
	}
}

// External Milvus configured: required. Not connected => False; connected => True.
func TestInfraReady_RequiresMilvusWhenURIConfigured(t *testing.T) {
	c := kserveClient(t, infraReadySts("corpus-nats", 1, 1), infraReadySts("corpus-redis", 1, 1))
	r := &infra.ReadyReconciler{Client: c}
	corpus := infraReadyCorpus()
	corpus.Spec.Infrastructure.Milvus = &accv1alpha1.MilvusSpec{URI: "milvus:19530"}

	corpus.Status.Infrastructure.MilvusConnected = false
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile (disconnected): %v", err)
	}
	if !statuspkg.IsConditionFalse(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady) {
		t.Error("expected InfrastructureReady=False when configured Milvus is unreachable")
	}

	corpus.Status.Infrastructure.MilvusConnected = true
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile (connected): %v", err)
	}
	if !statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady) {
		t.Error("expected InfrastructureReady=True once Milvus is connected")
	}
}

// End-to-end regression: with the condition SET by the reconciler (not hand-fed
// as a bool) plus CollectivesReady, a quiescent corpus computes to Ready. Before
// Finding B's fix the condition was absent → ComputeCorpusPhase never returned
// Ready. This is the assertion phase_test.go could not make.
func TestCorpusReachesReady_WithInfraConditionSet(t *testing.T) {
	c := kserveClient(t, infraReadySts("corpus-nats", 1, 1), infraReadySts("corpus-redis", 1, 1))
	corpus := infraReadyCorpus()
	if _, err := (&infra.ReadyReconciler{Client: c}).Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	// Stand in for the collective reconciler having set CollectivesReady.
	statuspkg.SetCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeCollectivesReady,
		metav1.ConditionTrue, "Evaluated", "all collectives are Ready")

	// Assemble PhaseInput exactly as agentcorpus_controller.go does.
	phase := statuspkg.ComputeCorpusPhase(statuspkg.PhaseInput{
		InfrastructureReady: statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady),
		CollectivesReady:    statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeCollectivesReady),
		PrerequisitesMet:    true,
		IsProgressing:       false,
	})
	if phase != accv1alpha1.CorpusPhaseReady {
		t.Fatalf("expected corpus phase Ready, got %s — InfrastructureReady wiring is broken", phase)
	}
}
