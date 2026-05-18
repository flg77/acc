// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package security

import (
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// buildCiliumL7Policy builds the Tier 3 CiliumNetworkPolicy: the FQDN
// egress of Tier 2, narrowed at L7 to HTTPS (TCP 443) with an HTTP rule
// that allows only the request methods ACC's LLM clients actually use.
//
// It supersedes the plain Tier 2 buildCiliumFQDNPolicy when Tier 3 is
// active — same object name, so Upsert replaces it in place.
func buildCiliumL7Policy(
	corpus *accv1alpha1.AgentCorpus, fqdns []string,
) *unstructured.Unstructured {
	var fqdnRules []interface{}
	for _, fqdn := range fqdns {
		fqdnRules = append(fqdnRules, map[string]interface{}{"matchName": fqdn})
	}

	// DNS visibility rule — identical to the Tier 2 policy.
	dnsRule := map[string]interface{}{
		"toEndpoints": []interface{}{
			map[string]interface{}{
				"matchLabels": map[string]interface{}{
					"k8s:io.kubernetes.pod.namespace": "kube-system",
					"k8s-app":                         "kube-dns",
				},
			},
		},
		"toPorts": []interface{}{
			map[string]interface{}{
				"ports": []interface{}{
					map[string]interface{}{"port": "53", "protocol": "ANY"},
				},
				"rules": map[string]interface{}{
					"dns": []interface{}{
						map[string]interface{}{"matchPattern": "*"},
					},
				},
			},
		},
	}

	// L7 egress: to the approved FQDNs, port 443, HTTP method-scoped.
	l7Egress := map[string]interface{}{
		"toFQDNs": fqdnRules,
		"toPorts": []interface{}{
			map[string]interface{}{
				"ports": []interface{}{
					map[string]interface{}{"port": "443", "protocol": "TCP"},
				},
				"rules": map[string]interface{}{
					"http": []interface{}{
						map[string]interface{}{"method": "GET"},
						map[string]interface{}{"method": "POST"},
					},
				},
			},
		},
	}

	spec := map[string]interface{}{
		"endpointSelector": map[string]interface{}{
			"matchLabels": map[string]interface{}{
				accv1alpha1.LabelManagedBy:  accv1alpha1.LabelManagedByVal,
				accv1alpha1.LabelCorpusName: corpus.Name,
			},
		},
		"egress": []interface{}{dnsRule, l7Egress},
	}

	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(gvkCiliumNetworkPolicy)
	u.SetName("acc-agent-fqdn-egress") // same name as Tier 2 — supersedes
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CommonLabels(corpus.Name, "networkpolicy", corpus.Spec.Version))
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	return u
}
