// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package v1alpha1

import (
	"fmt"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/validation/field"
	ctrl "sigs.k8s.io/controller-runtime"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/webhook"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"
)

var agentcorpuslog = logf.Log.WithName("agentcorpus-resource")

// SetupWebhookWithManager registers the webhook with the Manager.
func (r *AgentCorpus) SetupWebhookWithManager(mgr ctrl.Manager) error {
	return ctrl.NewWebhookManagedBy(mgr).
		For(r).
		Complete()
}

// +kubebuilder:webhook:path=/mutate-acc-redhat-io-v1alpha1-agentcorpus,mutating=true,failurePolicy=fail,sideEffects=None,groups=acc.redhat.io,resources=agentcorpora,verbs=create;update,versions=v1alpha1,name=magentcorpus.kb.io,admissionReviewVersions=v1

var _ webhook.Defaulter = &AgentCorpus{}

// Default implements webhook.Defaulter to set default values.
func (r *AgentCorpus) Default() {
	agentcorpuslog.Info("default", "name", r.Name)

	if r.Spec.DeployMode == "" {
		r.Spec.DeployMode = DeployModeStandalone
	}
	if r.Spec.Version == "" {
		r.Spec.Version = "0.1.0"
	}
	if r.Spec.ImageRegistry == "" {
		r.Spec.ImageRegistry = "registry.access.redhat.com"
	}
	if r.Spec.Infrastructure.NATS.Version == "" {
		r.Spec.Infrastructure.NATS.Version = "2.10"
	}
	if r.Spec.Infrastructure.NATS.Replicas == 0 {
		r.Spec.Infrastructure.NATS.Replicas = 1
	}
	if r.Spec.Infrastructure.NATS.StorageSize == "" {
		r.Spec.Infrastructure.NATS.StorageSize = "2Gi"
	}
	if r.Spec.Infrastructure.Redis.Version == "" {
		r.Spec.Infrastructure.Redis.Version = "6"
	}
	if r.Spec.Infrastructure.Redis.Replicas == 0 {
		r.Spec.Infrastructure.Redis.Replicas = 1
	}
	if r.Spec.Infrastructure.Redis.StorageSize == "" {
		r.Spec.Infrastructure.Redis.StorageSize = "1Gi"
	}
	if r.Spec.Governance.CategoryB.BundleServerImage == "" {
		r.Spec.Governance.CategoryB.BundleServerImage = "openpolicyagent/opa:latest"
	}
	if r.Spec.Governance.CategoryB.PollIntervalSeconds == 0 {
		r.Spec.Governance.CategoryB.PollIntervalSeconds = 30
	}
	if r.Spec.Governance.CategoryB.BundlePVCSize == "" {
		r.Spec.Governance.CategoryB.BundlePVCSize = "500Mi"
	}
	if r.Spec.Observability.Backend == "" {
		r.Spec.Observability.Backend = MetricsBackendLog
	}
	if r.Spec.UpgradePolicy.Mode == "" {
		r.Spec.UpgradePolicy.Mode = UpgradeModeAuto
	}
}

// +kubebuilder:webhook:path=/validate-acc-redhat-io-v1alpha1-agentcorpus,mutating=false,failurePolicy=fail,sideEffects=None,groups=acc.redhat.io,resources=agentcorpora,verbs=create;update,versions=v1alpha1,name=vagentcorpus.kb.io,admissionReviewVersions=v1

var _ webhook.Validator = &AgentCorpus{}

// ValidateCreate implements webhook.Validator.
func (r *AgentCorpus) ValidateCreate() (admission.Warnings, error) {
	agentcorpuslog.Info("validate create", "name", r.Name)
	return nil, r.validateAgentCorpus()
}

// ValidateUpdate implements webhook.Validator.
func (r *AgentCorpus) ValidateUpdate(old runtime.Object) (admission.Warnings, error) {
	agentcorpuslog.Info("validate update", "name", r.Name)
	return nil, r.validateAgentCorpus()
}

// ValidateDelete implements webhook.Validator.
func (r *AgentCorpus) ValidateDelete() (admission.Warnings, error) {
	return nil, nil
}

func (r *AgentCorpus) validateAgentCorpus() error {
	var allErrs field.ErrorList

	// rhoai mode requires Milvus URI
	if r.Spec.DeployMode == DeployModeRHOAI {
		if r.Spec.Infrastructure.Milvus == nil || r.Spec.Infrastructure.Milvus.URI == "" {
			allErrs = append(allErrs, field.Required(
				field.NewPath("spec", "infrastructure", "milvus", "uri"),
				"milvus.uri is required when deployMode=rhoai",
			))
		}
	}

	// gatekeeperIntegration requires wasmConfigMapRef
	if r.Spec.Governance.CategoryA.WASMConfigMapRef == "" {
		allErrs = append(allErrs, field.Required(
			field.NewPath("spec", "governance", "categoryA", "wasmConfigMapRef"),
			"wasmConfigMapRef must reference a ConfigMap containing the category_a.wasm blob",
		))
	}

	// otel backend requires collector endpoint
	if r.Spec.Observability.Backend == MetricsBackendOTel {
		if r.Spec.Observability.OTelCollector == nil || r.Spec.Observability.OTelCollector.Endpoint == "" {
			allErrs = append(allErrs, field.Required(
				field.NewPath("spec", "observability", "otelCollector", "endpoint"),
				"otelCollector.endpoint is required when observability.backend=otel",
			))
		}
	}

	// at least one collective required
	if len(r.Spec.Collectives) == 0 {
		allErrs = append(allErrs, field.Required(
			field.NewPath("spec", "collectives"),
			"at least one collective reference is required",
		))
	}

	// duplicate collective names not allowed
	seen := map[string]bool{}
	for i, c := range r.Spec.Collectives {
		if seen[c.Name] {
			allErrs = append(allErrs, field.Invalid(
				field.NewPath("spec", "collectives").Index(i).Child("name"),
				c.Name,
				fmt.Sprintf("duplicate collective name %q", c.Name),
			))
		}
		seen[c.Name] = true
	}

	if len(allErrs) == 0 {
		return nil
	}
	return apierrors.NewInvalid(
		schema.GroupKind{Group: "acc.redhat.io", Kind: "AgentCorpus"},
		r.Name, allErrs,
	)
}
