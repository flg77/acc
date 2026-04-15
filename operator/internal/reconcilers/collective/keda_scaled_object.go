// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

var scaledObjectGVK = schema.GroupVersionKind{
	Group:   "keda.sh",
	Version: "v1alpha1",
	Kind:    "ScaledObject",
}

// KEDAScaledObjectReconciler creates a ScaledObject per agent role when
// KEDA is installed and scaling is enabled on the collective.
//
// If KEDA is absent (corpus.Status.Prerequisites.KEDAInstalled=false), the
// reconciler is a complete no-op — the Warning event is already emitted by
// the PrerequisiteReconciler.
type KEDAScaledObjectReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// ReconcileCollective reconciles KEDA ScaledObjects for one collective.
func (r *KEDAScaledObjectReconciler) ReconcileCollective(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
) (struct{ ScaledObjectsActive bool }, error) {
	result := struct{ ScaledObjectsActive bool }{}

	// Guard: KEDA absent or scaling disabled.
	if !corpus.Status.Prerequisites.KEDAInstalled {
		return result, nil
	}
	if collective.Spec.Scaling == nil || !collective.Spec.Scaling.Enabled {
		return result, nil
	}

	scalingMap := buildScalingMap(collective)

	for _, roleSpec := range collective.Spec.Agents {
		role := roleSpec.Role
		deployName := fmt.Sprintf("%s-%s", collective.Name, string(role))

		// Look up per-role scaling config; fall back to defaults.
		rsc, ok := scalingMap[role]
		if !ok {
			rsc = accv1alpha1.RoleScalingSpec{
				Role:                     role,
				MinReplicas:              1,
				MaxReplicas:              10,
				NATSConsumerLagThreshold: 10,
				HealthMetricThreshold:    70,
			}
		}

		so := r.buildScaledObject(corpus, collective, deployName, rsc)
		if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, so, func(existing client.Object) error {
			desiredU := so.(*unstructured.Unstructured)
			existingU := existing.(*unstructured.Unstructured)
			spec, _, _ := unstructured.NestedMap(desiredU.Object, "spec")
			return unstructured.SetNestedMap(existingU.Object, spec, "spec")
		}); err != nil {
			return result, fmt.Errorf("upsert ScaledObject for %s/%s: %w", collective.Name, role, err)
		}
	}

	result.ScaledObjectsActive = true
	return result, nil
}

func (r *KEDAScaledObjectReconciler) buildScaledObject(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	deployName string,
	rsc accv1alpha1.RoleScalingSpec,
) client.Object {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(scaledObjectGVK)
	u.SetName(fmt.Sprintf("%s-keda", deployName))
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, "keda-scaled-object", corpus.Spec.Version))

	natsURL := fmt.Sprintf("nats://%s-nats.%s.svc.cluster.local:4222", corpus.Name, corpus.Namespace)
	consumerSubject := fmt.Sprintf("acc.%s.>", collective.Spec.CollectiveID)

	spec := map[string]interface{}{
		"scaleTargetRef": map[string]interface{}{
			"apiVersion": "apps/v1",
			"kind":       "Deployment",
			"name":       deployName,
		},
		"minReplicaCount": int64(rsc.MinReplicas),
		"maxReplicaCount": int64(rsc.MaxReplicas),
		"triggers": []interface{}{
			map[string]interface{}{
				"type": "nats-jetstream",
				"metadata": map[string]interface{}{
					"natsServerMonitoringEndpoint": fmt.Sprintf("%s-nats:8222", corpus.Name),
					"account":                      "$G",
					"stream":                       fmt.Sprintf("acc-%s", collective.Spec.CollectiveID),
					"consumer":                     fmt.Sprintf("%s-consumer", string(rsc.Role)),
					"lagThreshold":                 fmt.Sprintf("%d", rsc.NATSConsumerLagThreshold),
					"natsUrl":                      natsURL,
					"subject":                      consumerSubject,
				},
			},
		},
	}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	return u
}

func buildScalingMap(collective *accv1alpha1.AgentCollective) map[accv1alpha1.AgentRole]accv1alpha1.RoleScalingSpec {
	m := make(map[accv1alpha1.AgentRole]accv1alpha1.RoleScalingSpec)
	if collective.Spec.Scaling == nil {
		return m
	}
	for _, rs := range collective.Spec.Scaling.RoleScaling {
		m[rs.Role] = rs
	}
	return m
}
