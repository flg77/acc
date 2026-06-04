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
	"sort"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/yaml"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

var catalogLog = logf.Log.WithName("acccatalog-controller")

// CatalogsConfigMapName is the name of the per-namespace ConfigMap
// the reconciler writes; ACC pods mount this at
// /etc/acc/catalogs.yaml (system layer in the layered catalog
// resolver from acc/pkg/catalog.py).
const CatalogsConfigMapName = "acc-catalogs"

// AccCatalogReconciler watches every AccCatalog in a namespace and
// renders the merged catalog list to a single ConfigMap named
// "acc-catalogs" containing one YAML document with the catalogs:
// list, matching acc.pkg.catalog.CatalogFile.
//
// Whenever ANY AccCatalog in the namespace changes (created /
// updated / deleted), the reconciler re-walks all of them, sorts by
// priority desc (matching the Python resolver), and rewrites the
// ConfigMap atomically.  Status fields on each AccCatalog record
// the last-rendered timestamp + Reconciled condition.
//
// +kubebuilder:rbac:groups=acc.redhat.io,resources=acccatalogs,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=acc.redhat.io,resources=acccatalogs/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=core,resources=configmaps,verbs=get;list;watch;create;update;patch
type AccCatalogReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// SetupWithManager registers the reconciler.  We watch all
// AccCatalogs in the manager's namespace AND map every event to a
// per-namespace reconcile of the same sentinel key — that lets the
// reconciler always render the FULL set of catalogs, not just the
// one that triggered.
func (r *AccCatalogReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		Named("acccatalog").
		Watches(
			&accv1alpha1.AccCatalog{},
			handler.EnqueueRequestsFromMapFunc(r.mapToNamespaceSentinel),
			builder.WithPredicates(),
		).
		Complete(reconcile.Func(r.Reconcile))
}

// mapToNamespaceSentinel collapses every AccCatalog event into a
// single reconcile request per namespace.  The Reconcile method
// looks at the request namespace and re-renders the full list.
func (r *AccCatalogReconciler) mapToNamespaceSentinel(ctx context.Context, obj client.Object) []reconcile.Request {
	return []reconcile.Request{{
		NamespacedName: types.NamespacedName{
			Namespace: obj.GetNamespace(),
			Name:      CatalogsConfigMapName,
		},
	}}
}

// Reconcile re-renders the acc-catalogs ConfigMap for the request's
// namespace based on all AccCatalogs there.
func (r *AccCatalogReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := catalogLog.WithValues("namespace", req.Namespace)

	// List all AccCatalogs in the namespace.
	var list accv1alpha1.AccCatalogList
	if err := r.Client.List(ctx, &list, client.InNamespace(req.Namespace)); err != nil {
		return ctrl.Result{}, fmt.Errorf("listing AccCatalogs: %w", err)
	}

	// Build the YAML payload — sort by priority desc, then id asc for
	// deterministic ConfigMap content (matches acc.pkg.catalog
	// resolver's tie-break).
	entries := make([]accv1alpha1.AccCatalog, len(list.Items))
	copy(entries, list.Items)
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].Spec.Priority != entries[j].Spec.Priority {
			return entries[i].Spec.Priority > entries[j].Spec.Priority
		}
		return entries[i].Spec.CatalogID < entries[j].Spec.CatalogID
	})

	yamlBytes, err := renderCatalogsYAML(entries)
	if err != nil {
		return ctrl.Result{}, fmt.Errorf("rendering catalogs YAML: %w", err)
	}

	// Upsert the ConfigMap.
	cm := &corev1.ConfigMap{}
	cmKey := types.NamespacedName{Namespace: req.Namespace, Name: CatalogsConfigMapName}
	if err := r.Client.Get(ctx, cmKey, cm); err != nil {
		if !apierrors.IsNotFound(err) {
			return ctrl.Result{}, fmt.Errorf("fetching ConfigMap: %w", err)
		}
		cm = &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{
				Name:      CatalogsConfigMapName,
				Namespace: req.Namespace,
				Labels: map[string]string{
					"app.kubernetes.io/managed-by": "acc-operator",
					"app.kubernetes.io/component":  "catalog-config",
				},
			},
			Data: map[string]string{
				"catalogs.yaml": string(yamlBytes),
			},
		}
		if err := r.Client.Create(ctx, cm); err != nil {
			return ctrl.Result{}, fmt.Errorf("creating ConfigMap: %w", err)
		}
		log.Info("created ConfigMap", "entries", len(entries))
	} else {
		if cm.Data == nil {
			cm.Data = map[string]string{}
		}
		if cm.Data["catalogs.yaml"] != string(yamlBytes) {
			cm.Data["catalogs.yaml"] = string(yamlBytes)
			if err := r.Client.Update(ctx, cm); err != nil {
				return ctrl.Result{}, fmt.Errorf("updating ConfigMap: %w", err)
			}
			log.Info("updated ConfigMap", "entries", len(entries))
		}
	}

	// Status patch: mark each AccCatalog as reconciled.
	now := metav1.NewTime(time.Now().UTC())
	for i := range list.Items {
		item := &list.Items[i]
		item.Status.ObservedGeneration = item.Generation
		item.Status.LastRenderedAt = &now
		setCondition(&item.Status.Conditions, metav1.Condition{
			Type:               "Reconciled",
			Status:             metav1.ConditionTrue,
			Reason:             "Rendered",
			Message:            fmt.Sprintf("rendered into ConfigMap %s/%s", req.Namespace, CatalogsConfigMapName),
			LastTransitionTime: now,
		})
		if err := r.Client.Status().Update(ctx, item); err != nil {
			log.Error(err, "status update failed", "catalog", item.Name)
		}
	}

	return ctrl.Result{}, nil
}

// renderCatalogsYAML produces the YAML that mirrors acc.pkg.catalog
// CatalogFile — a top-level "catalogs:" list.
func renderCatalogsYAML(entries []accv1alpha1.AccCatalog) ([]byte, error) {
	type signerYAML struct {
		Issuer         string `yaml:"issuer" json:"issuer"`
		SubjectPattern string `yaml:"subject_pattern" json:"subject_pattern"`
		KeyPath        string `yaml:"key_path,omitempty" json:"key_path,omitempty"`
	}
	type catalogYAML struct {
		ID             string     `yaml:"id" json:"id"`
		Tier           string     `yaml:"tier" json:"tier"`
		Mode           string     `yaml:"mode" json:"mode"`
		URL            string     `yaml:"url,omitempty" json:"url,omitempty"`
		Path           string     `yaml:"path,omitempty" json:"path,omitempty"`
		RequiredSigner signerYAML `yaml:"required_signer" json:"required_signer"`
		Priority       int32      `yaml:"priority" json:"priority"`
	}
	type fileYAML struct {
		Catalogs []catalogYAML `yaml:"catalogs" json:"catalogs"`
	}

	out := fileYAML{Catalogs: make([]catalogYAML, 0, len(entries))}
	for _, e := range entries {
		out.Catalogs = append(out.Catalogs, catalogYAML{
			ID:   e.Spec.CatalogID,
			Tier: e.Spec.Tier,
			Mode: e.Spec.Mode,
			URL:  e.Spec.URL,
			Path: e.Spec.Path,
			RequiredSigner: signerYAML{
				Issuer:         e.Spec.RequiredSigner.Issuer,
				SubjectPattern: e.Spec.RequiredSigner.SubjectPattern,
				KeyPath:        e.Spec.RequiredSigner.KeyPath,
			},
			Priority: e.Spec.Priority,
		})
	}
	return yaml.Marshal(out)
}

// setCondition replaces or appends a condition by Type, matching the
// pattern other reconcilers use (meta.SetStatusCondition equivalent
// without pulling in the apimachinery helper).
func setCondition(conditions *[]metav1.Condition, c metav1.Condition) {
	for i, existing := range *conditions {
		if existing.Type == c.Type {
			// Preserve LastTransitionTime if status didn't change.
			if existing.Status == c.Status {
				c.LastTransitionTime = existing.LastTransitionTime
			}
			(*conditions)[i] = c
			return
		}
	}
	*conditions = append(*conditions, c)
}
