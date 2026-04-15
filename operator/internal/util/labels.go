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
