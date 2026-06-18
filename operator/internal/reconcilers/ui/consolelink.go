// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

import (
	"context"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// consoleLinkGVK identifies the OpenShift app-launcher link CR. Like the
// Route GVK it is referenced unstructured so the operator builds on
// vanilla Kubernetes (where console.openshift.io is absent) — the caller
// discovery-gates creation and tolerates a NoMatch error.
var consoleLinkGVK = schema.GroupVersionKind{
	Group:   "console.openshift.io",
	Version: "v1",
	Kind:    "ConsoleLink",
}

// consoleLinkSection groups every ACC link under one app-launcher heading
// so the WebGUI + TUI surface together as "independent menu" items
// (operator review 2026-06-16).
const consoleLinkSection = "Agentic Cell Corpus"

// routeHost returns the admitted external host of the named Route, or ""
// when the Route is absent or not yet admitted (no status.ingress[].host).
func routeHost(ctx context.Context, c client.Client, ns, name string) string {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(routeGVK)
	if err := c.Get(ctx, client.ObjectKey{Namespace: ns, Name: name}, u); err != nil {
		return ""
	}
	ingress, found, err := unstructured.NestedSlice(u.Object, "status", "ingress")
	if err != nil || !found || len(ingress) == 0 {
		return ""
	}
	first, ok := ingress[0].(map[string]interface{})
	if !ok {
		return ""
	}
	host, _, _ := unstructured.NestedString(first, "host")
	return host
}

// upsertConsoleLink creates/updates a cluster-scoped ConsoleLink in the
// OpenShift app-launcher (location ApplicationMenu) so an operator can
// open the surface straight from the console menu. ConsoleLink is
// cluster-scoped (no namespaced owner ref); it is labelled with the
// corpus name so a future finalizer can prune it. Returns the (possibly
// NoMatch) error for the caller to discovery-gate.
func upsertConsoleLink(ctx context.Context, c client.Client, corpus *accv1alpha1.AgentCorpus, linkName, text, href string) error {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(consoleLinkGVK)
	u.SetName(linkName)
	u.SetLabels(map[string]string{
		"acc.redhat.io/corpus":         corpus.Name,
		"app.kubernetes.io/managed-by": "acc-operator",
	})
	spec := map[string]interface{}{
		"location":        "ApplicationMenu",
		"text":            text,
		"href":            href,
		"applicationMenu": map[string]interface{}{"section": consoleLinkSection},
	}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	_, err := util.ClusterUpsert(ctx, c, u, func(existing client.Object) error {
		eu := existing.(*unstructured.Unstructured)
		return unstructured.SetNestedMap(eu.Object, spec, "spec")
	})
	return err
}
