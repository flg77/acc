// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package collective contains sub-reconcilers that manage the resources
// for each AgentCollective: agent Deployments, KEDA ScaledObjects, and
// KServe InferenceServices.
package collective

import (
	"context"
	"fmt"

	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
	"k8s.io/apimachinery/pkg/runtime"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// CollectiveReconciler fans out per-collective reconciliation.
// For each collective name listed in corpus.Spec.Collectives, it fetches
// the AgentCollective CR and delegates to AgentDeploymentReconciler,
// KEDAScaledObjectReconciler, and KServeReconciler.
type CollectiveReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *CollectiveReconciler) Name() string { return "collective" }

// Reconcile implements SubReconciler.
func (r *CollectiveReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	if corpus.Status.CollectiveStatuses == nil {
		corpus.Status.CollectiveStatuses = make(map[string]accv1alpha1.CollectiveStatus)
	}

	agentRec := &AgentDeploymentReconciler{Client: r.Client, Scheme: r.Scheme}
	kedaRec := &KEDAScaledObjectReconciler{Client: r.Client, Scheme: r.Scheme}
	kserveRec := &KServeReconciler{Client: r.Client, Scheme: r.Scheme}

	anyProgressing := false
	allReady := true

	for _, ref := range corpus.Spec.Collectives {
		collective := &accv1alpha1.AgentCollective{}
		if err := r.Client.Get(ctx, client.ObjectKey{
			Namespace: corpus.Namespace,
			Name:      ref.Name,
		}, collective); err != nil {
			return reconcilers.SubResult{}, fmt.Errorf("get AgentCollective %s: %w", ref.Name, err)
		}

		cs := corpus.Status.CollectiveStatuses[ref.Name]

		// Agent Deployments.
		agentRes, err := agentRec.ReconcileCollective(ctx, corpus, collective)
		if err != nil {
			return reconcilers.SubResult{}, fmt.Errorf("collective %s agent deployments: %w", ref.Name, err)
		}

		// KEDA ScaledObjects (skipped if KEDA absent).
		_, err = kedaRec.ReconcileCollective(ctx, corpus, collective)
		if err != nil {
			return reconcilers.SubResult{}, fmt.Errorf("collective %s keda: %w", ref.Name, err)
		}

		// KServe InferenceService (skipped if KServe absent or not vllm/llama_stack).
		kserveRes, err := kserveRec.ReconcileCollective(ctx, corpus, collective)
		if err != nil {
			return reconcilers.SubResult{}, fmt.Errorf("collective %s kserve: %w", ref.Name, err)
		}

		// Compute collective phase.
		cs.ReadyAgents = agentRes.ReadyAgents
		cs.DesiredAgents = agentRes.DesiredAgents
		cs.KServeReady = kserveRes.KServeReady

		totalReady := int32(0)
		totalDesired := int32(0)
		for _, v := range cs.ReadyAgents {
			totalReady += v
		}
		for _, v := range cs.DesiredAgents {
			totalDesired += v
		}

		kserveRequired := needsKServe(collective)
		cs.Phase = statuspkg.ComputeCollectivePhase(totalReady, totalDesired, kserveRequired, cs.KServeReady)

		statuspkg.SetCondition(&cs.Conditions, accv1alpha1.ConditionTypeReady,
			condStatus(cs.Phase == accv1alpha1.CollectivePhaseReady),
			string(cs.Phase), fmt.Sprintf("collective %s is %s", ref.Name, cs.Phase))

		corpus.Status.CollectiveStatuses[ref.Name] = cs

		if agentRes.Progressing {
			anyProgressing = true
		}
		if cs.Phase != accv1alpha1.CollectivePhaseReady {
			allReady = false
		}
	}

	// Update top-level conditions.
	statuspkg.SetCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeCollectivesReady,
		condStatus(allReady), "Evaluated", collectivesReadyMessage(allReady))

	return reconcilers.SubResult{Progressing: anyProgressing}, nil
}

func condStatus(ok bool) metav1.ConditionStatus {
	if ok {
		return metav1.ConditionTrue
	}
	return metav1.ConditionFalse
}

func collectivesReadyMessage(allReady bool) string {
	if allReady {
		return "all collectives are Ready"
	}
	return "one or more collectives are not yet Ready"
}

func needsKServe(collective *accv1alpha1.AgentCollective) bool {
	b := collective.Spec.LLM.Backend
	return b == accv1alpha1.LLMBackendVLLM || b == accv1alpha1.LLMBackendLlamaStack
}
