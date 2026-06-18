// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Regression tests for the agent-Deployment pod-template stability.
//
// The "endless collective ReplicaSets" bug was a non-deterministic manifest
// Volume projection: buildManifestDelivery built the items[] slice via
// `for key := range cm.Data`, and Go randomizes map iteration order, so the
// pod template churned on every reconcile and the Deployment controller spun
// up a new ReplicaSet each cycle. ProjectManifestItems must be deterministic.
package unit_test

import (
	"fmt"
	"reflect"
	"sort"
	"testing"

	corev1 "k8s.io/api/core/v1"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/manifests"
)

// manyKeyData returns a ConfigMap-shaped map with enough flattened keys that an
// unsorted (randomized) projection would, with overwhelming probability, differ
// across iterations — making the determinism assertion below a real regression
// guard rather than a coin-flip.
func manyKeyData() map[string]string {
	data := make(map[string]string, 24)
	for i := 0; i < 24; i++ {
		// Flattened keys, as ManifestDeliveryReconciler emits them.
		key := manifests.FlattenPath(fmt.Sprintf("persona%02d/role.yaml", i))
		data[key] = fmt.Sprintf("content-%d", i)
	}
	return data
}

// TestProjectManifestItems_Sorted asserts the projection is sorted by Key, which
// is what makes the pod template byte-stable across reconciles.
func TestProjectManifestItems_Sorted(t *testing.T) {
	data := manyKeyData()
	items := collective.ProjectManifestItems(data)

	if len(items) != len(data) {
		t.Fatalf("projected %d items, want %d", len(items), len(data))
	}

	keys := make([]string, len(items))
	for i, it := range items {
		keys[i] = it.Key
		// Path must be the unflattened key (slash-restored).
		if want := manifests.UnflattenKey(it.Key); it.Path != want {
			t.Errorf("item %q: Path=%q, want %q", it.Key, it.Path, want)
		}
	}
	if !sort.StringsAreSorted(keys) {
		t.Errorf("items not sorted by Key: %v", keys)
	}
}

// TestAgentPodSecurityContext_OpenShiftSCCSafe is the regression guard for the
// live RHOAI 3.4 finding: the operator hardcoded the agent pod's
// securityContext.runAsUser to 1001, which OpenShift's restricted-v2 SCC
// rejects ("must be in the ranges: [1000920000, 1000929999]") — so zero agent
// ReplicaSets ever produced a pod. The pod SecurityContext must NOT pin a fixed
// runAsUser/runAsGroup/fsGroup (those come from the namespace UID range), while
// still asserting non-root.
func TestAgentPodSecurityContext_OpenShiftSCCSafe(t *testing.T) {
	sc := collective.AgentPodSecurityContext()
	if sc == nil {
		t.Fatal("AgentPodSecurityContext returned nil")
	}
	if sc.RunAsUser != nil {
		t.Errorf("pod SecurityContext pins runAsUser=%d — OpenShift restricted-v2 SCC needs the namespace UID range injected (must be nil)", *sc.RunAsUser)
	}
	if sc.RunAsGroup != nil {
		t.Errorf("pod SecurityContext pins runAsGroup=%d — must be nil for SCC compatibility", *sc.RunAsGroup)
	}
	if sc.FSGroup != nil {
		t.Errorf("pod SecurityContext pins fsGroup=%d — must be nil for SCC compatibility", *sc.FSGroup)
	}
	if sc.RunAsNonRoot == nil || !*sc.RunAsNonRoot {
		t.Error("pod SecurityContext must keep runAsNonRoot: true")
	}
}

// TestAgentContainerSecurityContext_OpenShiftSCCSafe asserts the container-level
// context drops ALL capabilities, disallows privilege escalation, and — like the
// pod context — does NOT pin runAsUser, so the SCC-injected UID is honored.
func TestAgentContainerSecurityContext_OpenShiftSCCSafe(t *testing.T) {
	sc := collective.AgentContainerSecurityContext()
	if sc == nil {
		t.Fatal("AgentContainerSecurityContext returned nil")
	}
	if sc.RunAsUser != nil {
		t.Errorf("container SecurityContext pins runAsUser=%d — must be nil for OpenShift SCC compatibility", *sc.RunAsUser)
	}
	if sc.RunAsNonRoot == nil || !*sc.RunAsNonRoot {
		t.Error("container SecurityContext must keep runAsNonRoot: true")
	}
	if sc.AllowPrivilegeEscalation == nil || *sc.AllowPrivilegeEscalation {
		t.Error("container SecurityContext must set allowPrivilegeEscalation: false")
	}
	if sc.Capabilities == nil || len(sc.Capabilities.Drop) == 0 || sc.Capabilities.Drop[0] != corev1.Capability("ALL") {
		t.Errorf("container SecurityContext must drop ALL capabilities, got %+v", sc.Capabilities)
	}
}

// TestProjectManifestItems_Deterministic is the core regression: repeated
// projections of the same data must be byte-identical. Before the fix this
// failed because map iteration order is randomized per range loop.
func TestProjectManifestItems_Deterministic(t *testing.T) {
	data := manyKeyData()
	first := collective.ProjectManifestItems(data)
	for i := 0; i < 50; i++ {
		got := collective.ProjectManifestItems(data)
		if !reflect.DeepEqual(first, got) {
			t.Fatalf("projection #%d differs from first — pod template would churn:\n first=%v\n got=  %v",
				i, first, got)
		}
	}
}
