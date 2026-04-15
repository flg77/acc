// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package reconcilers

import (
	"context"
	"fmt"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// UpgradeReconciler manages the requireApproval annotation gate.
//
// When spec.upgradePolicy.requireApproval=true AND an infra version change
// is detected (NATS or Redis version strings differ from the observed state),
// the reconciler:
//   1. Sets status.pendingUpgradeVersion to the requested version.
//   2. Sets the UpgradeApprovalPending condition.
//   3. Returns Requeue=false to halt further reconciliation until the user
//      annotates the resource with acc.redhat.io/approve-upgrade=<version>.
//
// Agent-image-only upgrades (spec.version change, same infra versions) always
// proceed without approval, even when requireApproval=true.
type UpgradeReconciler struct {
	Client client.Client
}

// Name implements SubReconciler.
func (r *UpgradeReconciler) Name() string { return "upgrade" }

// Reconcile implements SubReconciler.
func (r *UpgradeReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (SubResult, error) {
	// -----------------------------------------------------------------------
	// Check if the user approved a pending upgrade.
	// -----------------------------------------------------------------------
	pendingVer := corpus.Status.PendingUpgradeVersion
	if pendingVer != "" {
		approveAnnotation := corpus.Annotations[accv1alpha1.AnnotationApproveUpgrade]
		if approveAnnotation == pendingVer {
			// Approval received — clear annotation, clear pending state.
			if err := r.clearApprovalAnnotation(ctx, corpus); err != nil {
				return SubResult{}, fmt.Errorf("clear approve-upgrade annotation: %w", err)
			}
			corpus.Status.PendingUpgradeVersion = ""
			statuspkg.RemoveCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeUpgradeApprovalPending)
			return SubResult{}, nil
		}

		// Pending upgrade still waiting for annotation — halt.
		statuspkg.SetCondition(
			&corpus.Status.Conditions,
			accv1alpha1.ConditionTypeUpgradeApprovalPending,
			metav1.ConditionTrue,
			"WaitingForApproval",
			fmt.Sprintf(
				"Infrastructure version change to %q requires approval. "+
					"Apply annotation %s=%s to proceed.",
				pendingVer, accv1alpha1.AnnotationApproveUpgrade, pendingVer,
			),
		)
		return SubResult{Requeue: false}, ErrUpgradeApprovalPending
	}

	// -----------------------------------------------------------------------
	// Detect infra version change vs. observed state.
	// -----------------------------------------------------------------------
	if !corpus.Spec.UpgradePolicy.RequireApproval {
		return SubResult{}, nil
	}

	observedNATS := corpus.Status.Infrastructure.NATSVersion
	observedRedis := corpus.Status.Infrastructure.RedisVersion
	desiredNATS := corpus.Spec.Infrastructure.NATS.Version
	desiredRedis := corpus.Spec.Infrastructure.Redis.Version

	if !util.InfraVersionChanged(observedNATS, desiredNATS, observedRedis, desiredRedis) {
		return SubResult{}, nil
	}

	// Infra version change detected AND requireApproval=true.
	pendingVer = desiredNATS // Use NATS version as the canonical "desired" marker.
	if desiredRedis != observedRedis {
		pendingVer = desiredRedis
	}

	corpus.Status.PendingUpgradeVersion = desiredNATS
	statuspkg.SetCondition(
		&corpus.Status.Conditions,
		accv1alpha1.ConditionTypeUpgradeApprovalPending,
		metav1.ConditionTrue,
		"ApprovalRequired",
		fmt.Sprintf(
			"Infrastructure version change detected (NATS: %s→%s, Redis: %s→%s). "+
				"Apply annotation %s=%s to approve.",
			observedNATS, desiredNATS, observedRedis, desiredRedis,
			accv1alpha1.AnnotationApproveUpgrade, pendingVer,
		),
	)
	return SubResult{Requeue: false}, ErrUpgradeApprovalPending
}

// clearApprovalAnnotation removes the approve-upgrade annotation from the
// live object to prevent re-triggering on the next reconcile.
func (r *UpgradeReconciler) clearApprovalAnnotation(ctx context.Context, corpus *accv1alpha1.AgentCorpus) error {
	patch := client.MergeFrom(corpus.DeepCopy())
	annotations := corpus.Annotations
	if annotations != nil {
		delete(annotations, accv1alpha1.AnnotationApproveUpgrade)
		corpus.Annotations = annotations
	}
	return r.Client.Patch(ctx, corpus, patch)
}

// ErrUpgradeApprovalPending is a sentinel error returned by UpgradeReconciler
// when the reconcile loop should halt until user approval. The parent
// reconciler checks for this error and skips remaining sub-reconcilers.
var ErrUpgradeApprovalPending = fmt.Errorf("upgrade approval pending")
