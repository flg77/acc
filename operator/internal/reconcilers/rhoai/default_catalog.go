// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package rhoai

import (
	"context"
	"fmt"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
)

const (
	// DefaultCatalogName is the bootstrapped AccCatalog object name.
	DefaultCatalogName = "acc-canonical"
	// LabelBootstrapped marks operator-bootstrapped objects.
	LabelBootstrapped = "acc.redhat.io/bootstrapped"
	// defaultCatalogURL is the published, signed ecosystem catalog.
	defaultCatalogURL = "https://flg77.github.io/acc-ecosystem"
)

// DefaultCatalogReconciler makes a fresh corpus package-ready out of the box:
// when NO AccCatalog exists in the corpus namespace, it creates the canonical
// signed ecosystem catalog (runs in every deploy mode, not just rhoai).
//
// CREATE-IF-ABSENT ONLY: it never updates an existing catalog and never
// recreates one while any other AccCatalog exists in the namespace, so user-
// or GitOps-managed catalogs always win. The created catalog carries NO
// ownerReference — it outlives the corpus by design.
type DefaultCatalogReconciler struct {
	Client client.Client
}

// Name implements SubReconciler.
func (r *DefaultCatalogReconciler) Name() string { return "rhoai/default-catalog" }

// Reconcile implements SubReconciler.
func (r *DefaultCatalogReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	if b := corpus.Spec.BootstrapDefaultCatalog; b != nil && !*b {
		return reconcilers.SubResult{}, nil
	}

	existing := &accv1alpha1.AccCatalogList{}
	if err := r.Client.List(ctx, existing, client.InNamespace(corpus.Namespace)); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("list AccCatalogs in %s: %w", corpus.Namespace, err)
	}
	if len(existing.Items) > 0 {
		corpus.Status.DefaultCatalogBootstrapped = true
		return reconcilers.SubResult{}, nil
	}

	catalog := &accv1alpha1.AccCatalog{
		ObjectMeta: metav1.ObjectMeta{
			Name:      DefaultCatalogName,
			Namespace: corpus.Namespace,
			Labels:    map[string]string{LabelBootstrapped: "true"},
		},
		Spec: accv1alpha1.AccCatalogSpec{
			CatalogID: DefaultCatalogName,
			Tier:      "trusted",
			Mode:      "https",
			URL:       defaultCatalogURL,
			Priority:  100,
			RequiredSigner: accv1alpha1.CatalogRequiredSigner{
				Issuer:         "https://token.actions.githubusercontent.com",
				SubjectPattern: "^https://github.com/flg77/acc-ecosystem/.*",
			},
		},
	}
	if err := r.Client.Create(ctx, catalog); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("bootstrap default AccCatalog: %w", err)
	}

	logf.FromContext(ctx).Info("bootstrapped default AccCatalog",
		"namespace", corpus.Namespace, "catalog", DefaultCatalogName)
	corpus.Status.DefaultCatalogBootstrapped = true
	return reconcilers.SubResult{}, nil
}
