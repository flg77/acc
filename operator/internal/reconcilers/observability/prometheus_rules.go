// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package observability

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

var prometheusRuleGVK = schema.GroupVersionKind{
	Group:   "monitoring.coreos.com",
	Version: "v1",
	Kind:    "PrometheusRule",
}

// PrometheusRulesReconciler creates a PrometheusRule CR when
// observability.prometheusRules=true and the monitoring.coreos.com API
// group is present. It is a no-op when Prometheus Operator is absent.
type PrometheusRulesReconciler struct {
	Client client.Client
}

// Name implements SubReconciler.
func (r *PrometheusRulesReconciler) Name() string { return "observability/prometheus-rules" }

// Reconcile implements SubReconciler.
func (r *PrometheusRulesReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	// Edge mode: Prometheus Operator is not available at the edge —
	// PrometheusRules are only rendered for datacenter (rhoai) deployments.
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge {
		return reconcilers.SubResult{}, nil
	}

	if !corpus.Spec.Observability.PrometheusRules {
		return reconcilers.SubResult{}, nil
	}
	if !corpus.Status.Prerequisites.PrometheusRulesSupported {
		// Warning already emitted by PrerequisiteReconciler — skip silently.
		return reconcilers.SubResult{}, nil
	}

	rule := r.buildPrometheusRule(corpus)
	if _, err := util.Upsert(ctx, r.Client, nil, corpus, rule, func(existing client.Object) error {
		desiredU := rule.(*unstructured.Unstructured)
		existingU := existing.(*unstructured.Unstructured)
		spec, _, _ := unstructured.NestedMap(desiredU.Object, "spec")
		return unstructured.SetNestedMap(existingU.Object, spec, "spec")
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert PrometheusRule: %w", err)
	}
	return reconcilers.SubResult{}, nil
}

func (r *PrometheusRulesReconciler) buildPrometheusRule(corpus *accv1alpha1.AgentCorpus) client.Object {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(prometheusRuleGVK)
	u.SetName(fmt.Sprintf("%s-acc-rules", corpus.Name))
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CommonLabels(corpus.Name, "prometheus-rules", corpus.Spec.Version))

	groups := []interface{}{
		map[string]interface{}{
			"name": "acc.agent.health",
			"rules": []interface{}{
				map[string]interface{}{
					"alert": "ACCAgentDown",
					"expr":  fmt.Sprintf(`absent(acc_agent_heartbeat_total{corpus="%s"}) == 1`, corpus.Name),
					"for":   "2m",
					"labels": map[string]interface{}{
						"severity": "warning",
						"corpus":   corpus.Name,
					},
					"annotations": map[string]interface{}{
						"summary":     "ACC agent heartbeat missing",
						"description": fmt.Sprintf("No heartbeat received from corpus %s for more than 2 minutes.", corpus.Name),
					},
				},
				map[string]interface{}{
					"alert": "ACCAgentHealthLow",
					"expr":  fmt.Sprintf(`avg by (collective_id, role) (acc_agent_health_score{corpus="%s"}) < 0.7`, corpus.Name),
					"for":   "5m",
					"labels": map[string]interface{}{
						"severity": "warning",
						"corpus":   corpus.Name,
					},
					"annotations": map[string]interface{}{
						"summary":     "ACC agent health score below threshold",
						"description": "Average health score for role {{ $labels.role }} in collective {{ $labels.collective_id }} is below 70%.",
					},
				},
				map[string]interface{}{
					"alert": "ACCGovernanceViolation",
					"expr":  fmt.Sprintf(`increase(acc_governance_violation_total{corpus="%s"}[5m]) > 0`, corpus.Name),
					"labels": map[string]interface{}{
						"severity": "critical",
						"corpus":   corpus.Name,
					},
					"annotations": map[string]interface{}{
						"summary":     "ACC governance violation detected",
						"description": "Category {{ $labels.category }} governance violation in corpus {{ $labels.corpus }}.",
					},
				},
			},
		},
		map[string]interface{}{
			"name": "acc.infra",
			"rules": []interface{}{
				map[string]interface{}{
					"alert": "ACCNATSDown",
					"expr":  fmt.Sprintf(`up{job="%s-nats"} == 0`, corpus.Name),
					"for":   "1m",
					"labels": map[string]interface{}{
						"severity": "critical",
						"corpus":   corpus.Name,
					},
					"annotations": map[string]interface{}{
						"summary": "ACC NATS JetStream is down",
					},
				},
			},
		},
	}

	_ = unstructured.SetNestedSlice(u.Object, groups, "spec", "groups")
	return u
}
