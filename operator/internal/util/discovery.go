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
	APIGroupKServe      = "serving.kserve.io"
	APIVersionKServe    = "serving.kserve.io/v1beta1"
	APIGroupMonitoring  = "monitoring.coreos.com"
	APIVersionPrometheusRule = "monitoring.coreos.com/v1"

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
