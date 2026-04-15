// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package controller

import (
	"context"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

var collectiveLog = logf.Log.WithName("agentcollective-controller")

// AgentCollectiveReconciler reconciles an AgentCollective object.
//
// The main reconciliation work for AgentCollective resources is performed by
// the CollectiveReconciler sub-reconciler, which is called from inside the
// AgentCorpusReconciler. This lightweight controller watches AgentCollective
// resources and triggers a re-reconcile of the owning AgentCorpus whenever a
// collective changes — ensuring the corpus status stays current.
//
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives/finalizers,verbs=update
type AgentCollectiveReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// SetupWithManager registers this reconciler with the Manager.
func (r *AgentCollectiveReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&accv1alpha1.AgentCollective{}).
		Owns(&appsv1.Deployment{}).
		Complete(r)
}

// Reconcile updates the AgentCollective status from its owned Deployments.
func (r *AgentCollectiveReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := collectiveLog.WithValues("agentcollective", req.NamespacedName)

	collective := &accv1alpha1.AgentCollective{}
	if err := r.Client.Get(ctx, req.NamespacedName, collective); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	original := collective.DeepCopy()

	// -----------------------------------------------------------------------
	// Aggregate ready / desired counts from owned Deployments.
	// -----------------------------------------------------------------------
	readyAgents := make(map[string]int32)
	desiredAgents := make(map[string]int32)

	for _, roleSpec := range collective.Spec.Agents {
		role := string(roleSpec.Role)
		deployName := fmt.Sprintf("%s-%s", collective.Name, role)

		deploy := &appsv1.Deployment{}
		if err := r.Client.Get(ctx, client.ObjectKey{
			Namespace: collective.Namespace,
			Name:      deployName,
		}, deploy); err != nil {
			if client.IgnoreNotFound(err) != nil {
				return ctrl.Result{}, err
			}
			// Deployment not yet created — treat as 0 ready.
			desiredAgents[role] = roleSpec.Replicas
			readyAgents[role] = 0
			continue
		}

		desiredAgents[role] = roleSpec.Replicas
		readyAgents[role] = deploy.Status.ReadyReplicas
	}

	collective.Status.ReadyAgents = readyAgents
	collective.Status.DesiredAgents = desiredAgents
	collective.Status.ObservedGeneration = collective.Generation

	// Compute phase.
	totalReady := int32(0)
	totalDesired := int32(0)
	for _, v := range readyAgents {
		totalReady += v
	}
	for _, v := range desiredAgents {
		totalDesired += v
	}
	collective.Status.Phase = statuspkg.ComputeCollectivePhase(totalReady, totalDesired, false, true)

	// Patch status.
	if err := statuspkg.PatchCollectiveStatus(ctx, r.Client, collective, original); err != nil {
		return ctrl.Result{}, err
	}

	log.V(1).Info("collective status updated",
		"phase", collective.Status.Phase,
		"ready", totalReady,
		"desired", totalDesired)

	return ctrl.Result{}, nil
}
