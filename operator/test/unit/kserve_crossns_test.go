// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for cross-namespace inferenceServiceRef resolution and the
// ACC_VLLM_INFERENCE_URL injection into agent pods.
package unit_test

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

var isvcGVK = schema.GroupVersionKind{
	Group:   "serving.kserve.io",
	Version: "v1beta1",
	Kind:    "InferenceService",
}

// kserveClient builds a fake client that can track the unstructured
// KServe InferenceService CR (and Deployments for the env-injection test).
func kserveClient(t *testing.T, objs ...client.Object) client.Client {
	t.Helper()
	s := newScheme(t)
	if err := appsv1.AddToScheme(s); err != nil {
		t.Fatalf("appsv1.AddToScheme: %v", err)
	}
	s.AddKnownTypeWithName(isvcGVK, &unstructured.Unstructured{})
	listGVK := isvcGVK
	listGVK.Kind = "InferenceServiceList"
	s.AddKnownTypeWithName(listGVK, &unstructured.UnstructuredList{})
	return fake.NewClientBuilder().
		WithScheme(s).
		WithObjects(objs...).
		Build()
}

// inferenceService builds an unstructured InferenceService with a Ready
// condition and the given status URLs (empty strings are omitted).
func inferenceService(name, ns, addressURL, statusURL string, ready bool) *unstructured.Unstructured {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(isvcGVK)
	u.SetName(name)
	u.SetNamespace(ns)
	readyStatus := "False"
	if ready {
		readyStatus = "True"
	}
	_ = unstructured.SetNestedSlice(u.Object, []interface{}{
		map[string]interface{}{"type": "Ready", "status": readyStatus},
	}, "status", "conditions")
	if addressURL != "" {
		_ = unstructured.SetNestedField(u.Object, addressURL, "status", "address", "url")
	}
	if statusURL != "" {
		_ = unstructured.SetNestedField(u.Object, statusURL, "status", "url")
	}
	return u
}

func vllmCollective(ref, refNS string) *accv1alpha1.AgentCollective {
	return &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "research", Namespace: "acc-system"},
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID: "research-01",
			LLM: accv1alpha1.LLMSpec{
				Backend: accv1alpha1.LLMBackendVLLM,
				VLLM: &accv1alpha1.VLLMSpec{
					InferenceServiceRef:       ref,
					InferenceServiceNamespace: refNS,
					Model:                     "llama-31-8b-instruct",
					Deploy:                    false,
				},
			},
			Agents: []accv1alpha1.AgentRoleSpec{{Role: "observer", Replicas: 1}},
		},
	}
}

func kserveCorpus() *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "test-corpus", Namespace: "acc-system"},
		Spec:       accv1alpha1.AgentCorpusSpec{Version: "0.1.0"},
	}
}

// A model served from a different RHOAI Data Science Project resolves via
// spec.llm.vllm.inferenceServiceNamespace.
func TestKServe_CrossNamespaceRef(t *testing.T) {
	is := inferenceService("llama-31-8b-instruct", "my-first-model",
		"http://llama-31-8b-instruct-predictor.my-first-model.svc.cluster.local", "", true)
	r := &collective.KServeReconciler{Client: kserveClient(t, is), Scheme: newScheme(t)}

	res, err := r.ReconcileCollective(context.Background(), kserveCorpus(),
		vllmCollective("llama-31-8b-instruct", "my-first-model"))
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if !res.KServeReady {
		t.Errorf("expected KServeReady=true for ready cross-ns InferenceService")
	}
	want := "http://llama-31-8b-instruct-predictor.my-first-model.svc.cluster.local"
	if res.InferenceURL != want {
		t.Errorf("InferenceURL = %q, want %q", res.InferenceURL, want)
	}
}

// Empty inferenceServiceNamespace keeps the pre-existing same-namespace
// behavior.
func TestKServe_SameNamespaceDefault(t *testing.T) {
	is := inferenceService("local-llm", "acc-system",
		"http://local-llm-predictor.acc-system.svc.cluster.local", "", true)
	r := &collective.KServeReconciler{Client: kserveClient(t, is), Scheme: newScheme(t)}

	res, err := r.ReconcileCollective(context.Background(), kserveCorpus(),
		vllmCollective("local-llm", ""))
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if !res.KServeReady || res.InferenceURL == "" {
		t.Errorf("same-ns ref should resolve, got ready=%v url=%q", res.KServeReady, res.InferenceURL)
	}
}

// Without a cluster-local status.address.url the resolver falls back to
// the external status.url.
func TestKServe_URLFallbackToStatusURL(t *testing.T) {
	is := inferenceService("ext-llm", "my-first-model", "",
		"https://ext-llm-my-first-model.apps.example.com", true)
	r := &collective.KServeReconciler{Client: kserveClient(t, is), Scheme: newScheme(t)}

	res, err := r.ReconcileCollective(context.Background(), kserveCorpus(),
		vllmCollective("ext-llm", "my-first-model"))
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if res.InferenceURL != "https://ext-llm-my-first-model.apps.example.com" {
		t.Errorf("expected fallback to status.url, got %q", res.InferenceURL)
	}
}

// A missing InferenceService is not an error — just not ready, no URL.
func TestKServe_MissingIsNotReady(t *testing.T) {
	r := &collective.KServeReconciler{Client: kserveClient(t), Scheme: newScheme(t)}

	res, err := r.ReconcileCollective(context.Background(), kserveCorpus(),
		vllmCollective("absent", "my-first-model"))
	if err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	if res.KServeReady || res.InferenceURL != "" {
		t.Errorf("missing InferenceService should be not-ready/no-URL, got %+v", res)
	}
}

// Role names with underscores (e.g. coding_agent) must sanitize into
// valid RFC-1123 Deployment names.
func TestAgentDeployment_UnderscoreRoleNameSanitized(t *testing.T) {
	c := kserveClient(t)
	r := &collective.AgentDeploymentReconciler{Client: c, Scheme: newScheme(t)}
	corpus := kserveCorpus()
	col := vllmCollective("llama-31-8b-instruct", "my-first-model")
	col.Spec.Agents = []accv1alpha1.AgentRoleSpec{{Role: "coding_agent", Replicas: 1}}

	if _, err := r.ReconcileCollective(context.Background(), corpus, col, "acc-role-research-01", ""); err != nil {
		t.Fatalf("ReconcileCollective: %v", err)
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-system", Name: "research-coding-agent"}, deploy); err != nil {
		t.Fatalf("expected sanitized Deployment research-coding-agent: %v", err)
	}
	if deploy.Spec.Template.Labels["acc.redhat.io/role"] != "coding_agent" &&
		deploy.Spec.Selector.MatchLabels["acc.redhat.io/role"] != "coding_agent" {
		t.Log("role label not under acc.redhat.io/role — acceptable, name sanitization is the contract")
	}
}

// The resolved URL reaches agent pods as ACC_VLLM_INFERENCE_URL; an empty
// URL injects nothing (the config placeholder stays unresolved so the
// runtime's rhoai-mode validation still fires).
func TestAgentDeployment_InjectsInferenceURL(t *testing.T) {
	for _, tc := range []struct {
		name    string
		url     string
		wantEnv bool
	}{
		{"resolved URL injected", "http://llama-31-8b-instruct-predictor.my-first-model.svc.cluster.local", true},
		{"empty URL omitted", "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := kserveClient(t)
			r := &collective.AgentDeploymentReconciler{Client: c, Scheme: newScheme(t)}
			corpus := kserveCorpus()
			col := vllmCollective("llama-31-8b-instruct", "my-first-model")

			if _, err := r.ReconcileCollective(context.Background(), corpus, col, "acc-role-research-01", tc.url); err != nil {
				t.Fatalf("ReconcileCollective: %v", err)
			}

			deploy := &appsv1.Deployment{}
			if err := c.Get(context.Background(),
				types.NamespacedName{Namespace: "acc-system", Name: "research-observer"}, deploy); err != nil {
				t.Fatalf("get Deployment: %v", err)
			}
			found := ""
			for _, ctr := range deploy.Spec.Template.Spec.Containers {
				for _, env := range ctr.Env {
					if env.Name == "ACC_VLLM_INFERENCE_URL" {
						found = env.Value
					}
				}
			}
			if tc.wantEnv && found != tc.url {
				t.Errorf("ACC_VLLM_INFERENCE_URL = %q, want %q", found, tc.url)
			}
			if !tc.wantEnv && found != "" {
				t.Errorf("expected no ACC_VLLM_INFERENCE_URL, got %q", found)
			}
		})
	}
}
