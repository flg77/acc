// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	"fmt"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// SandboxGVK is the Agent Sandbox Kubernetes SIG `Sandbox` custom resource
// (sandboxes.agents.x-k8s.io) that OpenShell's Kubernetes driver provisions
// each kernel-enforced sandbox through. OpenShell ships no policy CRD of its
// own — the sandbox is this upstream SIG object, and OpenShell's `combined`
// topology injects the supervisor + Landlock/seccomp/netns capabilities into
// the materialized pod (the supervisor runs IN the agent pod). Pinned against
// the live CRD on acc1 (Phase-0/3 spike).
var SandboxGVK = schema.GroupVersionKind{
	Group:   "agents.x-k8s.io",
	Version: "v1beta1",
	Kind:    "Sandbox",
}

const (
	// annotationSandboxID keys the sandbox for the gateway: the OpenShell
	// supervisor fetches its SandboxPolicy from the gateway under this id, and
	// the operator delivers the emitted policy (BuildSandboxPolicyYAML) to the
	// gateway under the same id — the companion policy-delivery seam.
	annotationSandboxID = "openshell.io/sandbox-id"

	// operatingModeRunning / -Suspended are the Sandbox spec's lifecycle states;
	// they map onto ACC's resume / hibernate (threading hibernate here is a
	// later refinement).
	operatingModeRunning = "Running"

	// shutdownPolicyDelete removes the sandbox pod when the Sandbox object is
	// deleted (the operator owns the Sandbox via an owner ref).
	shutdownPolicyDelete = "Delete"
)

// BuildSandboxObject emits the Agent Sandbox `Sandbox` CR that runs the agent
// AS an OpenShell kernel-enforced sandbox: its spec.podTemplate IS the agent's
// pod template (combined topology). It is an unstructured object — mirroring
// the OVN/Cilium emit in security/fqdn_egress.go — so the operator does not
// vendor the Agent Sandbox Go types for a single CRD.
//
// This is the Phase-3 ATTACH seam. It does not deliver the policy: that is the
// companion gateway call, keyed by the same sandbox id (annotationSandboxID).
func BuildSandboxObject(
	corpus *accv1alpha1.AgentCorpus, name string, podTemplate corev1.PodTemplateSpec,
) (*unstructured.Unstructured, error) {
	tmpl, err := runtime.DefaultUnstructuredConverter.ToUnstructured(&podTemplate)
	if err != nil {
		return nil, fmt.Errorf("convert agent pod template for sandbox %s: %w", name, err)
	}

	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(SandboxGVK)
	u.SetName(name)
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CommonLabels(corpus.Name, "sandbox", corpus.Spec.Version))
	u.SetAnnotations(map[string]string{annotationSandboxID: name})

	if err := unstructured.SetNestedMap(u.Object, tmpl, "spec", "podTemplate"); err != nil {
		return nil, fmt.Errorf("set sandbox podTemplate for %s: %w", name, err)
	}
	_ = unstructured.SetNestedField(u.Object, operatingModeRunning, "spec", "operatingMode")
	_ = unstructured.SetNestedField(u.Object, shutdownPolicyDelete, "spec", "shutdownPolicy")
	return u, nil
}
