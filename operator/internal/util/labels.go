// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package util

import (
	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// CommonLabels returns the standard set of labels applied to all resources
// managed by the ACC operator.
func CommonLabels(corpusName, component, version string) map[string]string {
	return map[string]string{
		accv1alpha1.LabelManagedBy: accv1alpha1.LabelManagedByVal,
		accv1alpha1.LabelComponent: component,
		accv1alpha1.LabelVersion:   version,
		accv1alpha1.LabelCorpusName: corpusName,
	}
}

// SelectorLabels returns the subset of labels safe to use in an immutable
// Pod/Service selector: the version label is stripped.  A Deployment's or
// StatefulSet's spec.selector is immutable, so leaving the version label in the
// selector means a version bump (which patches the pod-template labels to the
// new version) diverges the template labels from the frozen selector — the API
// server then rejects the update with "`selector` does not match template
// `labels`" and the whole reconcile chain aborts at the first such workload.
// The version stays in the object + pod-template labels (via CommonLabels); only
// the selector drops it, giving a stable identity that survives upgrades.
// Accepts any label map (CommonLabels / CollectiveLabels / AgentLabels).
func SelectorLabels(labels map[string]string) map[string]string {
	out := make(map[string]string, len(labels))
	for k, v := range labels {
		if k == accv1alpha1.LabelVersion {
			continue
		}
		out[k] = v
	}
	return out
}

// CollectiveLabels returns labels for resources that belong to a specific
// AgentCollective within a corpus.
func CollectiveLabels(corpusName, collectiveID, component, version string) map[string]string {
	labels := CommonLabels(corpusName, component, version)
	labels[accv1alpha1.LabelCollectiveID] = collectiveID
	return labels
}

// AgentLabels returns labels for agent Deployment pods — includes the role.
func AgentLabels(corpusName, collectiveID string, role accv1alpha1.AgentRole, version string) map[string]string {
	labels := CollectiveLabels(corpusName, collectiveID, string(role), version)
	labels[accv1alpha1.LabelAgentRole] = string(role)
	return labels
}

// KagentiAgentLabel returns the discovery label Kagenti's operator watches on
// workloads (`kagenti.io/type: agent`).  Applied to an agent Deployment's
// ObjectMeta + pod-template labels (NOT its selector — selector labels are
// immutable) when AgentCollectiveSpec.Kagenti.Enabled is true.  See
// OpenSpec 20260527-agentcard-discovery (Phase 1) and docs/kagenti-discovery.md.
func KagentiAgentLabel() map[string]string {
	return map[string]string{
		accv1alpha1.LabelKagentiType: accv1alpha1.LabelKagentiTypeAgent,
	}
}

// MergeLabels merges one or more label maps, with later maps winning on
// duplicate keys. The first argument is not mutated.
func MergeLabels(base map[string]string, overrides ...map[string]string) map[string]string {
	result := make(map[string]string, len(base))
	for k, v := range base {
		result[k] = v
	}
	for _, m := range overrides {
		for k, v := range m {
			result[k] = v
		}
	}
	return result
}
