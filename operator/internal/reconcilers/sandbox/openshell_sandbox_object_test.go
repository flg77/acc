// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

func TestBuildSandboxObject(t *testing.T) {
	corpus := &accv1alpha1.AgentCorpus{}
	corpus.Name = "demo"
	corpus.Namespace = "acc-proj"
	corpus.Spec.Version = "0.1.0"

	podTemplate := corev1.PodTemplateSpec{
		Spec: corev1.PodSpec{
			Containers: []corev1.Container{{Name: "agent", Image: "acc/agent:latest"}},
		},
	}

	u, err := BuildSandboxObject(corpus, "demo-coding", podTemplate)
	if err != nil {
		t.Fatalf("BuildSandboxObject: %v", err)
	}

	// The emitted object is the Agent Sandbox Sandbox CR, identified + owned.
	if gvk := u.GroupVersionKind(); gvk != SandboxGVK {
		t.Errorf("GVK = %v, want %v", gvk, SandboxGVK)
	}
	if u.GetName() != "demo-coding" || u.GetNamespace() != "acc-proj" {
		t.Errorf("name/ns = %s/%s, want demo-coding/acc-proj", u.GetName(), u.GetNamespace())
	}
	if got := u.GetAnnotations()[annotationSandboxID]; got != "demo-coding" {
		t.Errorf("%s = %q, want demo-coding", annotationSandboxID, got)
	}
	if got := u.GetLabels()[accv1alpha1.LabelManagedBy]; got != accv1alpha1.LabelManagedByVal {
		t.Errorf("managed-by = %q, want %q", got, accv1alpha1.LabelManagedByVal)
	}
	if got := u.GetLabels()[accv1alpha1.LabelCorpusName]; got != "demo" {
		t.Errorf("corpus-name label = %q, want demo", got)
	}

	// operatingMode defaults to Running (resume; Suspended = hibernate later).
	if mode, _, _ := unstructured.NestedString(u.Object, "spec", "operatingMode"); mode != operatingModeRunning {
		t.Errorf("operatingMode = %q, want %q", mode, operatingModeRunning)
	}

	// The agent's pod template IS the sandbox workload (combined topology): it
	// round-trips into spec.podTemplate intact.
	containers, found, err := unstructured.NestedSlice(u.Object, "spec", "podTemplate", "spec", "containers")
	if err != nil || !found || len(containers) != 1 {
		t.Fatalf("spec.podTemplate.spec.containers: found=%v len=%d err=%v", found, len(containers), err)
	}
	c0, ok := containers[0].(map[string]interface{})
	if !ok || c0["name"] != "agent" || c0["image"] != "acc/agent:latest" {
		t.Errorf("container = %v, want agent / acc/agent:latest", containers[0])
	}
}
