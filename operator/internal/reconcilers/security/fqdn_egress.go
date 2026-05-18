// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package security

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// Tier 2 backends.
const (
	backendEgressFirewall = "egressfirewall"
	backendCilium         = "cilium"
)

// GVKs for the third-party policy CRDs the operator emits as
// unstructured objects (it does not vendor the Cilium / OVN Go types —
// that would pull in a very large dependency tree for two CRDs).
var (
	gvkEgressFirewall = schema.GroupVersionKind{
		Group: "k8s.ovn.org", Version: "v1", Kind: "EgressFirewall",
	}
	gvkCiliumNetworkPolicy = schema.GroupVersionKind{
		Group: "cilium.io", Version: "v2", Kind: "CiliumNetworkPolicy",
	}
)

// defaultExternalLLMFQDNs is the built-in allow-set for Tier 2 external
// egress when spec.networkPolicy.allowedExternalLLM is empty.
func defaultExternalLLMFQDNs() []string {
	return []string{
		"api.anthropic.com",
		"api.openai.com",
	}
}

// externalEgressFQDNs returns the full FQDN allow-set: the external-LLM
// set (operator override or the built-in default) plus any
// extraEgressFQDNs.
func externalEgressFQDNs(np *accv1alpha1.NetworkPolicySpec) []string {
	llm := np.AllowedExternalLLM
	if len(llm) == 0 {
		llm = defaultExternalLLMFQDNs()
	}
	out := append([]string{}, llm...)
	out = append(out, np.ExtraEgressFQDNs...)
	return out
}

// buildEgressFirewall builds the OVN-Kubernetes EgressFirewall for the
// ACC namespace: allow the approved FQDNs (and any extra CIDRs), deny
// the rest of the public internet.  EgressFirewall governs only
// cluster-egress traffic and must be named "default" (OVN requirement).
func buildEgressFirewall(
	corpus *accv1alpha1.AgentCorpus, fqdns []string,
) *unstructured.Unstructured {
	np := corpus.Spec.NetworkPolicy
	var egress []interface{}
	for _, fqdn := range fqdns {
		egress = append(egress, map[string]interface{}{
			"type": "Allow",
			"to":   map[string]interface{}{"dnsName": fqdn},
		})
	}
	for _, cidr := range np.ExtraEgressCIDRs {
		egress = append(egress, map[string]interface{}{
			"type": "Allow",
			"to":   map[string]interface{}{"cidrSelector": cidr},
		})
	}
	// Final rule — deny everything else leaving the cluster.
	egress = append(egress, map[string]interface{}{
		"type": "Deny",
		"to":   map[string]interface{}{"cidrSelector": "0.0.0.0/0"},
	})

	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(gvkEgressFirewall)
	u.SetName("default") // OVN requires exactly this name, one per ns
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CommonLabels(corpus.Name, "networkpolicy", corpus.Spec.Version))
	_ = unstructured.SetNestedSlice(u.Object, egress, "spec", "egress")
	return u
}

// buildCiliumFQDNPolicy builds a CiliumNetworkPolicy that restricts
// agent-pod external egress to the approved FQDNs.  Cilium FQDN
// matching needs a companion DNS-visibility rule (toEndpoints kube-dns
// with a DNS L7 rule) so the agent proxy can observe the lookups.
func buildCiliumFQDNPolicy(
	corpus *accv1alpha1.AgentCorpus, fqdns []string,
) *unstructured.Unstructured {
	var fqdnRules []interface{}
	for _, fqdn := range fqdns {
		fqdnRules = append(fqdnRules, map[string]interface{}{"matchName": fqdn})
	}

	// DNS visibility: allow egress to kube-dns on 53 with an L7 DNS
	// rule so Cilium's DNS proxy learns the FQDN→IP mapping.
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
	fqdnEgress := map[string]interface{}{
		"toFQDNs": fqdnRules,
	}

	spec := map[string]interface{}{
		"endpointSelector": map[string]interface{}{
			"matchLabels": map[string]interface{}{
				accv1alpha1.LabelManagedBy:  accv1alpha1.LabelManagedByVal,
				accv1alpha1.LabelCorpusName: corpus.Name,
			},
		},
		"egress": []interface{}{dnsRule, fqdnEgress},
	}

	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(gvkCiliumNetworkPolicy)
	u.SetName("acc-agent-fqdn-egress")
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CommonLabels(corpus.Name, "networkpolicy", corpus.Spec.Version))
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	return u
}

// upsertUnstructured upserts a third-party-CRD object, copying the
// desired spec on update.
func (r *NetworkPolicyReconciler) upsertUnstructured(
	ctx context.Context, corpus *accv1alpha1.AgentCorpus, desired *unstructured.Unstructured,
) error {
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, desired,
		func(existing client.Object) error {
			ex := existing.(*unstructured.Unstructured)
			spec, found, _ := unstructured.NestedMap(desired.Object, "spec")
			if found {
				_ = unstructured.SetNestedMap(ex.Object, spec, "spec")
			}
			return nil
		}); err != nil {
		return fmt.Errorf("upsert %s %s: %w",
			desired.GetKind(), desired.GetName(), err)
	}
	return nil
}
