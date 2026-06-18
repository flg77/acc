// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package infra

import (
	"context"
	"fmt"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

// ReadyReconciler aggregates infrastructure health into the
// InfrastructureReady condition (proposal 032 G1).
//
// ComputeCorpusPhase gates the corpus Ready phase on this condition
// (status/phase.go rule 3). Before this reconciler existed NOTHING set the
// condition: the NATS/Redis reconcilers only returned SubResult.Progressing,
// and IsConditionTrue(conditions, InfrastructureReady) reads an ABSENT
// condition as false — so rule 3 always tripped and a corpus could never
// reach Ready (it pinned at Progressing while anything was progressing, or
// Degraded once quiescent). The pure-function phase unit test masked the gap
// by passing the bool in directly; nothing exercised the controller wiring.
//
// It must run AFTER the NATS/Redis/Milvus slots in buildSubReconcilers so the
// StatefulSets it inspects already exist this pass.
type ReadyReconciler struct {
	Client client.Client
}

// Name implements SubReconciler.
func (r *ReadyReconciler) Name() string { return "infra/ready" }

// Reconcile sets ConditionTypeInfrastructureReady from the live readiness of
// the operator-managed infrastructure workloads.
//
// Backend-aware: NATS and Redis are always operator-managed StatefulSets and
// are always required. Milvus is NEVER operator-installed (MilvusReconciler
// only probes an external instance), so it is required only when an external
// Milvus URI is configured — turbovec/lancedb backends leave it nil and do not
// gate readiness on it (proposal 024 default rhoai backend is turbovec).
func (r *ReadyReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	var notReady []string

	for _, comp := range []string{"nats", "redis"} {
		ready, err := r.statefulSetReady(ctx, corpus.Namespace, fmt.Sprintf("%s-%s", corpus.Name, comp))
		if err != nil {
			return reconcilers.SubResult{}, fmt.Errorf("check %s readiness: %w", comp, err)
		}
		if !ready {
			notReady = append(notReady, comp)
		}
	}

	if m := corpus.Spec.Infrastructure.Milvus; m != nil && m.URI != "" {
		if !corpus.Status.Infrastructure.MilvusConnected {
			notReady = append(notReady, "milvus")
		}
	}

	if len(notReady) == 0 {
		statuspkg.SetCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady,
			metav1.ConditionTrue, "Ready",
			"NATS, Redis and any configured Milvus are healthy")
		return reconcilers.SubResult{}, nil
	}

	statuspkg.SetCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady,
		metav1.ConditionFalse, "Progressing",
		fmt.Sprintf("waiting for infrastructure: %s", strings.Join(notReady, ", ")))
	// Progressing=true so ComputeCorpusPhase yields Progressing (actively
	// working) rather than Degraded while the StatefulSets come up.
	return reconcilers.SubResult{Progressing: true}, nil
}

// statefulSetReady reports whether the named StatefulSet has all desired
// replicas Ready. A not-yet-created StatefulSet (NotFound) counts as not
// ready: the reconciler that owns it runs earlier in the same pass, so a
// genuine miss means it is still being created and the corpus is progressing.
func (r *ReadyReconciler) statefulSetReady(ctx context.Context, ns, name string) (bool, error) {
	sts := &appsv1.StatefulSet{}
	if err := r.Client.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, sts); err != nil {
		if apierrors.IsNotFound(err) {
			return false, nil
		}
		return false, err
	}
	desired := int32(1)
	if sts.Spec.Replicas != nil {
		desired = *sts.Spec.Replicas
	}
	return sts.Status.ReadyReplicas >= desired, nil
}
