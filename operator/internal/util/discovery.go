// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package util

import (
	"context"
	"fmt"
	"net"
	"time"

	"k8s.io/client-go/discovery"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const (
	// API group / version strings for optional prerequisites.
	APIGroupKEDA        = "keda.sh"
	APIVersionKEDA      = "keda.sh/v1alpha1"
	APIGroupGatekeeper  = "templates.gatekeeper.sh"
	APIVersionGatekeeper = "templates.gatekeeper.sh/v1"
	APIGroupRHOAI       = "datasciencecluster.opendatahub.io"
	// The ODH/RHOAI dashboard registers OdhApplication (tiles) under this
	// group; OdhQuickStart ships with the same dashboard component.
	APIGroupOdhDashboard = "dashboard.opendatahub.io"
	APIGroupKServe      = "serving.kserve.io"
	APIVersionKServe    = "serving.kserve.io/v1beta1"
	APIGroupMonitoring  = "monitoring.coreos.com"
	APIVersionPrometheusRule = "monitoring.coreos.com/v1"
	// spire-controller-manager registers the ClusterSPIFFEID +
	// ClusterFederatedTrustDomain CRDs under this group (proposal 011).
	APIGroupSpire       = "spire.spiffe.io"
	APIVersionSpire     = "spire.spiffe.io/v1alpha1"

	// Cilium registers CiliumNetworkPolicy etc. under this group;
	// OVN-Kubernetes registers EgressFirewall under k8s.ovn.org.
	// Calico (a policy-enforcing CNI) uses projectcalico.org.  All
	// three feed the proposal-014 network-policy capability tiers.
	APIGroupCilium  = "cilium.io"
	APIGroupOVN     = "k8s.ovn.org"
	APIGroupCalico  = "projectcalico.org"

	// Runtime-evidence backends (proposal 015).  RHACS and NetObserv
	// have their own API groups.  Tetragon registers TracingPolicy
	// under cilium.io/v1alpha1 — SHARED with the Cilium CNI — so
	// Tetragon must be detected at the *resource* level (the
	// TracingPolicy kind), never by the cilium.io group.
	APIGroupRHACS         = "platform.stackrox.io"
	APIGroupNetObserv     = "flows.netobserv.io"
	APIVersionTetragon    = "cilium.io/v1alpha1"
	KindTracingPolicy     = "TracingPolicy"

	tcpDialTimeout = 3 * time.Second
)

// APIGroupChecker wraps the Kubernetes discovery client with helpers for
// detecting whether optional CRD groups are installed.
type APIGroupChecker struct {
	disc discovery.DiscoveryInterface
}

// NewAPIGroupChecker builds a checker from a controller-runtime Client.
// The client must expose a REST mapper; we obtain a discovery client from
// the manager's config in the controller setup.
func NewAPIGroupChecker(disc discovery.DiscoveryInterface) *APIGroupChecker {
	return &APIGroupChecker{disc: disc}
}

// HasAPIGroup returns true when any version of the given group is registered
// in the cluster. It uses the cached discovery client where available.
func (c *APIGroupChecker) HasAPIGroup(group string) (bool, error) {
	groups, err := c.disc.ServerGroups()
	if err != nil {
		return false, fmt.Errorf("discovery.ServerGroups: %w", err)
	}
	for _, g := range groups.Groups {
		if g.Name == group {
			return true, nil
		}
	}
	return false, nil
}

// HasAPIVersion returns true when the specific group/version tuple is present.
func (c *APIGroupChecker) HasAPIVersion(groupVersion string) (bool, error) {
	_, resourceList, err := c.disc.ServerGroupsAndResources()
	if err != nil {
		// discovery may return partial results + error — tolerate it
		if resourceList == nil {
			return false, fmt.Errorf("discovery.ServerGroupsAndResources: %w", err)
		}
	}
	for _, rl := range resourceList {
		if rl.GroupVersion == groupVersion {
			return true, nil
		}
	}
	return false, nil
}

// KEDAInstalled returns true when the KEDA API group is available.
func (c *APIGroupChecker) KEDAInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupKEDA)
}

// GatekeeperInstalled returns true when the Gatekeeper ConstraintTemplate
// API group is available.
func (c *APIGroupChecker) GatekeeperInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupGatekeeper)
}

// RHOAIInstalled returns true when the DataScienceCluster API group
// (ODH / RHOAI operator) is available.
func (c *APIGroupChecker) RHOAIInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupRHOAI)
}

// KServeInstalled returns true when the KServe InferenceService API is
// available — required for vllm/llama_stack LLM backends in rhoai mode.
func (c *APIGroupChecker) KServeInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupKServe)
}

// PrometheusRulesSupported returns true when monitoring.coreos.com CRDs
// are present (Prometheus Operator installed).
func (c *APIGroupChecker) PrometheusRulesSupported() (bool, error) {
	return c.HasAPIGroup(APIGroupMonitoring)
}

// SpireInstalled returns true when the spire.spiffe.io API group is
// registered — i.e. spire-controller-manager (and its ClusterSPIFFEID
// CRD) is present.  Required for SPIFFE workload identity (proposal
// 011); the SpiffeReconciler is a no-op when this is false.
func (c *APIGroupChecker) SpireInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupSpire)
}

// HasAPIResource returns true when a specific resource *kind* is
// registered under the given group/version.  Unlike HasAPIGroup, this
// distinguishes co-tenants of one API group — e.g. Tetragon's
// TracingPolicy vs Cilium's CiliumNetworkPolicy, both under cilium.io
// (proposal 015).
func (c *APIGroupChecker) HasAPIResource(groupVersion, kind string) (bool, error) {
	_, resourceList, err := c.disc.ServerGroupsAndResources()
	if err != nil && resourceList == nil {
		return false, fmt.Errorf("discovery.ServerGroupsAndResources: %w", err)
	}
	for _, rl := range resourceList {
		if rl.GroupVersion != groupVersion {
			continue
		}
		for _, r := range rl.APIResources {
			if r.Kind == kind {
				return true, nil
			}
		}
	}
	return false, nil
}

// RHACSInstalled returns true when the platform.stackrox.io API group
// is registered — Red Hat Advanced Cluster Security, a proposal-015
// runtime-evidence backend.
func (c *APIGroupChecker) RHACSInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupRHACS)
}

// NetObservInstalled returns true when the flows.netobserv.io API
// group is registered — OpenShift Network Observability, the
// proposal-015 network-connect evidence source.
func (c *APIGroupChecker) NetObservInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupNetObserv)
}

// TetragonInstalled returns true when the TracingPolicy resource kind
// is registered under cilium.io/v1alpha1 — a proposal-015
// runtime-evidence backend.  Detected resource-level, NOT via
// HasAPIGroup("cilium.io"), which would false-positive on any Cilium
// CNI cluster that has no Tetragon.
func (c *APIGroupChecker) TetragonInstalled() (bool, error) {
	return c.HasAPIResource(APIVersionTetragon, KindTracingPolicy)
}

// CiliumInstalled returns true when the cilium.io API group is
// registered — i.e. the Cilium CNI (with its CiliumNetworkPolicy CRD)
// is present.  Enables proposal-014 Tier 2/3 network policy.
func (c *APIGroupChecker) CiliumInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupCilium)
}

// OVNEgressFirewallSupported returns true when the k8s.ovn.org API
// group is registered — i.e. the cluster runs OVN-Kubernetes, which
// enforces standard NetworkPolicy and offers the EgressFirewall CRD
// for Tier 2 FQDN egress (proposal 014).
func (c *APIGroupChecker) OVNEgressFirewallSupported() (bool, error) {
	return c.HasAPIGroup(APIGroupOVN)
}

// CalicoInstalled returns true when the projectcalico.org API group is
// registered — Calico is a policy-enforcing CNI.  Used only by the
// CNI-enforcement heuristic (proposal 014), not as a policy backend.
func (c *APIGroupChecker) CalicoInstalled() (bool, error) {
	return c.HasAPIGroup(APIGroupCalico)
}

// CNIEnforcesNetworkPolicy is the heuristic for "does the running CNI
// actually enforce Kubernetes NetworkPolicy objects" (proposal 014).
//
// There is no portable API that answers this directly.  OVN-Kubernetes,
// Cilium, and Calico all enforce NetworkPolicy and all register a
// recognisable API group; Flannel (the K3s default) enforces nothing
// and registers no policy API group.  So: NetworkPolicy is enforced
// iff one of those three policy-capable CNIs is detected.
//
// The verdict is heuristic — operators override it with
// spec.networkPolicy.cniEnforces ("true"/"false") when the inference
// is wrong (e.g. a NetworkPolicy-capable CNI this code does not know).
func (c *APIGroupChecker) CNIEnforcesNetworkPolicy() (bool, error) {
	for _, group := range []string{APIGroupOVN, APIGroupCilium, APIGroupCalico} {
		ok, err := c.HasAPIGroup(group)
		if err != nil {
			return false, err
		}
		if ok {
			return true, nil
		}
	}
	return false, nil
}

// TCPReachable dials addr (host:port) and returns true when the connection
// succeeds within tcpDialTimeout. Used to probe Kafka bootstrap servers and
// external Milvus.
func TCPReachable(_ context.Context, addr string) bool {
	conn, err := net.DialTimeout("tcp", addr, tcpDialTimeout)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// KafkaReachable probes the first bootstrap server in bootstrapServers
// (comma-separated host:port list) and returns whether it is reachable.
func KafkaReachable(ctx context.Context, bootstrapServers string) bool {
	if bootstrapServers == "" {
		return false
	}
	// Only probe the first server for liveness check.
	first := bootstrapServers
	for i, ch := range bootstrapServers {
		if ch == ',' {
			first = bootstrapServers[:i]
			break
		}
	}
	return TCPReachable(ctx, first)
}

// Ensure client import is used — the checker is constructed from manager config,
// but callers that hold a client.Client can call this helper to get a discovery
// client from the REST config embedded in the manager.
var _ client.Client // compile-time import guard (not used directly here)
