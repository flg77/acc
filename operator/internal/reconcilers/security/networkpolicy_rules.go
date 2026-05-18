// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package security contains the proposal-014 NetworkPolicyReconciler:
// the capability-tiered network-isolation layer for ACC agent pods.
package security

import (
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// Tier 1 policy object names — stable so Upsert is idempotent.
const (
	policyDefaultDeny   = "acc-agent-default-deny"
	policyAllowDNS      = "acc-agent-allow-dns"
	policyAllowInNS     = "acc-agent-allow-in-namespace"
	policyAllowExternal = "acc-agent-allow-external-https"
)

// RFC1918 ranges — excluded from the coarse external-HTTPS egress rule
// so Tier 1 "external 443" does not silently re-open in-cluster traffic.
var rfc1918 = []string{
	"10.0.0.0/8",
	"172.16.0.0/12",
	"192.168.0.0/16",
}

// agentPodSelector matches every ACC agent pod in a corpus: operator-
// managed, this corpus, and carrying an agent-role label.
func agentPodSelector(corpus *accv1alpha1.AgentCorpus) metav1.LabelSelector {
	return metav1.LabelSelector{
		MatchLabels: map[string]string{
			accv1alpha1.LabelManagedBy:  accv1alpha1.LabelManagedByVal,
			accv1alpha1.LabelCorpusName: corpus.Name,
		},
		MatchExpressions: []metav1.LabelSelectorRequirement{{
			Key:      accv1alpha1.LabelAgentRole,
			Operator: metav1.LabelSelectorOpExists,
		}},
	}
}

func protoPtr(p corev1.Protocol) *corev1.Protocol { return &p }

func port(n int32) intstr.IntOrString { return intstr.FromInt32(n) }

// buildTier1Policies returns the Tier 1 (L3/L4) NetworkPolicy set for a
// corpus's agent pods: default-deny both directions, then additive
// allows for DNS, same-namespace traffic (NATS/Redis/LLM/Milvus/OTel
// all live there), and coarse external HTTPS for external LLM APIs.
//
// Caller sets namespace, labels, and owner references via util.Upsert.
func buildTier1Policies(corpus *accv1alpha1.AgentCorpus) []*networkingv1.NetworkPolicy {
	sel := agentPodSelector(corpus)
	labels := util.CommonLabels(corpus.Name, "networkpolicy", corpus.Spec.Version)
	ns := corpus.Namespace

	meta := func(name string) metav1.ObjectMeta {
		return metav1.ObjectMeta{Name: name, Namespace: ns, Labels: labels}
	}

	// 1. default-deny — selects agent pods, both policy types, no rules.
	defaultDeny := &networkingv1.NetworkPolicy{
		ObjectMeta: meta(policyDefaultDeny),
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: sel,
			PolicyTypes: []networkingv1.PolicyType{
				networkingv1.PolicyTypeIngress,
				networkingv1.PolicyTypeEgress,
			},
		},
	}

	// 2. allow-dns — egress to port 53 (UDP+TCP) anywhere.  DNS must
	//    resolve before any Service or FQDN lookup works — this rule is
	//    emitted first and unconditionally (the #1 footgun).
	allowDNS := &networkingv1.NetworkPolicy{
		ObjectMeta: meta(policyAllowDNS),
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: sel,
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeEgress},
			Egress: []networkingv1.NetworkPolicyEgressRule{{
				Ports: []networkingv1.NetworkPolicyPort{
					{Protocol: protoPtr(corev1.ProtocolUDP), Port: ptrIOS(port(53))},
					{Protocol: protoPtr(corev1.ProtocolTCP), Port: ptrIOS(port(53))},
				},
			}},
		},
	}

	// 3. allow-in-namespace — ingress from + egress to the ACC
	//    namespace.  This is the Tier 1 isolation boundary: agents talk
	//    freely to NATS/Redis/LLM/Milvus/OTel and to each other, but a
	//    pod in any *other* namespace cannot reach them.
	nsPeer := []networkingv1.NetworkPolicyPeer{{
		NamespaceSelector: &metav1.LabelSelector{
			MatchLabels: map[string]string{
				"kubernetes.io/metadata.name": ns,
			},
		},
	}}
	allowInNS := &networkingv1.NetworkPolicy{
		ObjectMeta: meta(policyAllowInNS),
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: sel,
			PolicyTypes: []networkingv1.PolicyType{
				networkingv1.PolicyTypeIngress,
				networkingv1.PolicyTypeEgress,
			},
			Ingress: []networkingv1.NetworkPolicyIngressRule{{From: nsPeer}},
			Egress:  []networkingv1.NetworkPolicyEgressRule{{To: nsPeer}},
		},
	}

	// 4. allow-external-https — coarse egress to 443 on non-RFC1918
	//    destinations, for external LLM APIs (Anthropic, OpenAI-
	//    compatible).  Tier 1 cannot scope this to specific hostnames —
	//    that is Tier 2 (FQDN egress).  RFC1918 is excepted so this
	//    rule never silently re-opens in-cluster reachability.
	except := append([]string{}, rfc1918...)
	allowExternal := &networkingv1.NetworkPolicy{
		ObjectMeta: meta(policyAllowExternal),
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: sel,
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeEgress},
			Egress: []networkingv1.NetworkPolicyEgressRule{{
				To: []networkingv1.NetworkPolicyPeer{{
					IPBlock: &networkingv1.IPBlock{
						CIDR:   "0.0.0.0/0",
						Except: except,
					},
				}},
				Ports: []networkingv1.NetworkPolicyPort{
					{Protocol: protoPtr(corev1.ProtocolTCP), Port: ptrIOS(port(443))},
				},
			}},
		},
	}

	return []*networkingv1.NetworkPolicy{
		defaultDeny, allowDNS, allowInNS, allowExternal,
	}
}

func ptrIOS(v intstr.IntOrString) *intstr.IntOrString { return &v }
