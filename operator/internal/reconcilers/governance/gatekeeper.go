// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package governance

import (
	"context"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

var constraintTemplateGVK = schema.GroupVersionKind{
	Group:   "templates.gatekeeper.sh",
	Version: "v1",
	Kind:    "ConstraintTemplate",
}

// GatekeeperReconciler creates the three ACC ConstraintTemplates when
// OPA Gatekeeper is installed. It is cluster-scoped: ConstraintTemplates
// are cluster resources and cannot be owned by a namespaced object.
//
// If Gatekeeper is absent, it is a no-op (Warning already emitted by
// PrerequisiteReconciler).
type GatekeeperReconciler struct {
	Client client.Client
}

// Name implements SubReconciler.
func (r *GatekeeperReconciler) Name() string { return "governance/gatekeeper" }

// Reconcile implements SubReconciler.
func (r *GatekeeperReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	// Edge mode: Gatekeeper ConstraintTemplates are not available on
	// MicroShift / K3s edge deployments — skip without logging.
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge {
		return reconcilers.SubResult{}, nil
	}

	// Skip if Gatekeeper is not installed or integration is disabled.
	if !corpus.Status.Prerequisites.GatekeeperInstalled {
		return reconcilers.SubResult{}, nil
	}
	if !corpus.Spec.Governance.GatekeeperIntegration {
		return reconcilers.SubResult{}, nil
	}

	templates := accConstraintTemplates(corpus.Name)
	for _, ct := range templates {
		if _, err := util.ClusterUpsert(ctx, r.Client, ct, func(existing client.Object) error {
			// ConstraintTemplates are largely immutable after creation.
			// We update only the spec.targets[0].rego field.
			existingU := existing.(*unstructured.Unstructured)
			desiredU := ct.(*unstructured.Unstructured)
			spec, _, _ := unstructured.NestedMap(desiredU.Object, "spec")
			_ = unstructured.SetNestedMap(existingU.Object, spec, "spec")
			return nil
		}); err != nil {
			return reconcilers.SubResult{}, err
		}
	}

	return reconcilers.SubResult{}, nil
}

// accConstraintTemplates returns the three ACC ConstraintTemplate objects
// as unstructured resources so we don't need the Gatekeeper Go types as
// a compile-time dependency.
func accConstraintTemplates(_ string) []client.Object {
	// Category A: signal schema validation (immutable WASM enforced in-process;
	// the CT provides an additional Kubernetes admission backstop).
	catA := constraintTemplate("acc-category-a-signal-schema", `
package acc.catA
violation[{"msg": msg}] {
  not input.review.object.metadata.labels["acc.redhat.io/collective-id"]
  msg := "agent resource is missing the acc.redhat.io/collective-id label"
}`)

	// Category B: OPA bundle policy.
	catB := constraintTemplate("acc-category-b-bundle-policy", `
package acc.catB
violation[{"msg": msg}] {
  input.review.object.metadata.labels["app.kubernetes.io/managed-by"] != "acc-operator"
  input.review.object.kind == "Deployment"
  msg := "Deployment in ACC namespace must be managed by acc-operator"
}`)

	// Category C: confidence threshold (advisory — warns rather than blocks).
	catC := constraintTemplate("acc-category-c-confidence", `
package acc.catC
warn[{"msg": msg}] {
  score := to_number(input.review.object.metadata.annotations["acc.redhat.io/confidence-score"])
  score < 0.8
  msg := sprintf("confidence score %v is below threshold 0.80", [score])
}`)

	return []client.Object{catA, catB, catC}
}

func constraintTemplate(name, rego string) client.Object {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(constraintTemplateGVK)
	u.SetName(name)
	_ = unstructured.SetNestedField(u.Object, name, "spec", "crd", "spec", "names", "kind")
	_ = unstructured.SetNestedSlice(u.Object, []interface{}{
		map[string]interface{}{
			"target": "admission.k8s.gatekeeper.sh",
			"rego":   rego,
		},
	}, "spec", "targets")
	return u
}
