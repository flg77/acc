// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

var inferenceServiceGVK = schema.GroupVersionKind{
	Group:   "serving.kserve.io",
	Version: "v1beta1",
	Kind:    "InferenceService",
}

// KServeResult carries KServe readiness back to CollectiveReconciler.
type KServeResult struct {
	KServeReady bool
}

// KServeReconciler creates a KServe InferenceService when the collective's
// LLM backend is vllm and vllm.deploy=true, and KServe is installed.
//
// If KServe is absent, the reconciler is a no-op and KServeReady remains false.
type KServeReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// ReconcileCollective reconciles the InferenceService for one collective.
func (r *KServeReconciler) ReconcileCollective(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
) (KServeResult, error) {
	result := KServeResult{}

	// Only vllm backend triggers InferenceService creation.
	llm := collective.Spec.LLM
	if llm.Backend != accv1alpha1.LLMBackendVLLM || llm.VLLM == nil {
		// LlamaStack uses an external endpoint — no InferenceService to manage.
		result.KServeReady = true // treat as "not needed → satisfied"
		return result, nil
	}

	vllm := llm.VLLM

	// If deploy=false, user manages the InferenceService themselves.
	// We just check its readiness.
	if !vllm.Deploy {
		ready, err := r.checkInferenceServiceReady(ctx, corpus.Namespace, vllm.InferenceServiceRef)
		if err != nil {
			return result, err
		}
		result.KServeReady = ready
		return result, nil
	}

	// KServe must be installed to create an InferenceService.
	if !corpus.Status.Prerequisites.KServeInstalled {
		return result, nil
	}

	isName := vllm.InferenceServiceRef
	if isName == "" {
		isName = fmt.Sprintf("%s-%s-llm", collective.Name, collective.Spec.CollectiveID)
	}

	is := r.buildInferenceService(corpus, collective, isName, vllm)
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, collective, is, func(existing client.Object) error {
		desiredU := is.(*unstructured.Unstructured)
		existingU := existing.(*unstructured.Unstructured)
		spec, _, _ := unstructured.NestedMap(desiredU.Object, "spec")
		return unstructured.SetNestedMap(existingU.Object, spec, "spec")
	}); err != nil {
		return result, fmt.Errorf("upsert InferenceService %s: %w", isName, err)
	}

	ready, err := r.checkInferenceServiceReady(ctx, corpus.Namespace, isName)
	if err != nil {
		return result, err
	}
	result.KServeReady = ready
	return result, nil
}

func (r *KServeReconciler) buildInferenceService(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	name string,
	vllm *accv1alpha1.VLLMSpec,
) client.Object {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(inferenceServiceGVK)
	u.SetName(name)
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(util.CollectiveLabels(corpus.Name, collective.Spec.CollectiveID, "inference-service", corpus.Spec.Version))

	predictor := map[string]interface{}{
		"model": map[string]interface{}{
			"modelFormat": map[string]interface{}{
				"name": "vllm",
			},
			"storageUri": fmt.Sprintf("pvc://%s", vllm.ModelStoragePVC),
			"args": []interface{}{
				"--model", vllm.Model,
				"--dtype", "float16",
				"--max-model-len", "4096",
			},
		},
	}

	// Inject resource requirements if specified.
	if vllm.Resources != nil {
		resources := map[string]interface{}{}
		if len(vllm.Resources.Limits) > 0 {
			limits := map[string]interface{}{}
			for k, v := range vllm.Resources.Limits {
				limits[string(k)] = v.String()
			}
			resources["limits"] = limits
		}
		if len(vllm.Resources.Requests) > 0 {
			requests := map[string]interface{}{}
			for k, v := range vllm.Resources.Requests {
				requests[string(k)] = v.String()
			}
			resources["requests"] = requests
		}
		_ = unstructured.SetNestedMap(predictor["model"].(map[string]interface{}), resources, "resources")
	}

	spec := map[string]interface{}{"predictor": predictor}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	return u
}

// checkInferenceServiceReady reads the InferenceService and checks its
// Ready condition. Returns (false, nil) when the resource doesn't exist yet.
func (r *KServeReconciler) checkInferenceServiceReady(ctx context.Context, ns, name string) (bool, error) {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(inferenceServiceGVK)
	if err := r.Client.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, u); err != nil {
		return false, client.IgnoreNotFound(err)
	}

	// KServe sets status.conditions[type=Ready].status=True when ready.
	conditions, _, _ := unstructured.NestedSlice(u.Object, "status", "conditions")
	for _, c := range conditions {
		cond, ok := c.(map[string]interface{})
		if !ok {
			continue
		}
		if cond["type"] == "Ready" && cond["status"] == "True" {
			return true, nil
		}
	}
	return false, nil
}
