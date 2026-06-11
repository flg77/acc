// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package rhoai integrates a corpus with RHOAI (OpenShift AI): the corpus
// namespace becomes a Data Science Project, a default package catalog is
// bootstrapped, and the RHOAI dashboard gains an ACC tile + quickstarts.
package rhoai

import (
	"context"
	"encoding/json"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
)

const (
	// LabelDashboard marks a namespace as an RHOAI Data Science Project.
	LabelDashboard      = "opendatahub.io/dashboard"
	LabelDashboardValue = "true"
	// AnnotationDisplayName is the project display name RHOAI shows.
	AnnotationDisplayName = "openshift.io/display-name"
)

// ProjectReconciler registers the corpus namespace as an RHOAI Data Science
// Project by labeling it opendatahub.io/dashboard=true (display name via the
// openshift.io/display-name annotation when configured).
//
// Semantics are ADDITIVE-ONLY: the label is never removed — not on corpus
// deletion and not when registration is later disabled — because other RHOAI
// assets may live in the namespace.
type ProjectReconciler struct {
	Client client.Client
	// Reader is an uncached reader (manager APIReader) used for the
	// namespace GET so the cluster-wide manager never starts a Namespace
	// informer. Falls back to Client when nil (unit tests).
	Reader client.Reader
}

// Name implements SubReconciler.
func (r *ProjectReconciler) Name() string { return "rhoai/project" }

// Reconcile implements SubReconciler.
func (r *ProjectReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	if corpus.Spec.DeployMode != accv1alpha1.DeployModeRHOAI {
		return reconcilers.SubResult{}, nil
	}
	if !corpus.Status.Prerequisites.RHOAIInstalled {
		return reconcilers.SubResult{}, nil
	}
	// nil block and nil pointer both mean enabled (corpora created before
	// 0.1.4 carry no rhoai block; the CRD default is true).
	if rh := corpus.Spec.RHOAI; rh != nil && rh.RegisterNamespaceAsProject != nil && !*rh.RegisterNamespaceAsProject {
		return reconcilers.SubResult{}, nil
	}

	reader := r.Reader
	if reader == nil {
		reader = r.Client
	}

	ns := &corev1.Namespace{}
	if err := reader.Get(ctx, types.NamespacedName{Name: corpus.Namespace}, ns); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("get namespace %s: %w", corpus.Namespace, err)
	}

	displayName := ""
	if corpus.Spec.RHOAI != nil {
		displayName = corpus.Spec.RHOAI.ProjectDisplayName
	}

	labeled := ns.Labels[LabelDashboard] == LabelDashboardValue
	named := displayName == "" || ns.Annotations[AnnotationDisplayName] == displayName
	if labeled && named {
		corpus.Status.RHOAIProjectRegistered = true
		return reconcilers.SubResult{}, nil
	}

	// RFC 7386 merge patch touching ONLY our two keys — every other label,
	// annotation, and metadata owner is preserved.
	meta := map[string]interface{}{
		"labels": map[string]string{LabelDashboard: LabelDashboardValue},
	}
	if displayName != "" {
		meta["annotations"] = map[string]string{AnnotationDisplayName: displayName}
	}
	payload, err := json.Marshal(map[string]interface{}{"metadata": meta})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("marshal namespace patch: %w", err)
	}
	if err := r.Client.Patch(ctx, ns, client.RawPatch(types.MergePatchType, payload)); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("label namespace %s as Data Science Project: %w", corpus.Namespace, err)
	}

	logf.FromContext(ctx).V(1).Info("registered namespace as RHOAI Data Science Project",
		"namespace", corpus.Namespace, "displayName", displayName)
	corpus.Status.RHOAIProjectRegistered = true
	return reconcilers.SubResult{}, nil
}
