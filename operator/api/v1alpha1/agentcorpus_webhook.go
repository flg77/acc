// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package v1alpha1

import (
	"context"
	"fmt"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/validation/field"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/webhook"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"
)

var agentcorpuslog = logf.Log.WithName("agentcorpus-resource")

// SetupWebhookWithManager registers the webhook with the Manager.
// Defaulting uses a client-backed CustomDefaulter so it can detect RHOAI on the
// cluster; validation stays on the AgentCorpus type (webhook.Validator).
func (r *AgentCorpus) SetupWebhookWithManager(mgr ctrl.Manager) error {
	return ctrl.NewWebhookManagedBy(mgr).
		For(r).
		WithDefaulter(&AgentCorpusCustomDefaulter{Client: mgr.GetClient()}).
		Complete()
}

// +kubebuilder:webhook:path=/mutate-acc-redhat-io-v1alpha1-agentcorpus,mutating=true,failurePolicy=fail,sideEffects=None,groups=acc.redhat.io,resources=agentcorpora,verbs=create;update,versions=v1alpha1,name=magentcorpus.kb.io,admissionReviewVersions=v1

// AgentCorpusCustomDefaulter applies defaults to AgentCorpus resources. It holds
// a client so it can probe the cluster — notably to detect RHOAI (a
// DataScienceCluster) and default deployMode accordingly.
type AgentCorpusCustomDefaulter struct {
	Client client.Client
}

var _ admission.CustomDefaulter = &AgentCorpusCustomDefaulter{}

// Default implements admission.CustomDefaulter.
func (d *AgentCorpusCustomDefaulter) Default(ctx context.Context, obj runtime.Object) error {
	r, ok := obj.(*AgentCorpus)
	if !ok {
		return fmt.Errorf("expected an AgentCorpus object but got %T", obj)
	}
	agentcorpuslog.Info("default", "name", r.Name)

	// deployMode: when unset, auto-detect — "rhoai" if the cluster runs RHOAI /
	// OpenShift AI (a DataScienceCluster exists), else "standalone".
	if r.Spec.DeployMode == "" {
		r.Spec.DeployMode = d.detectDeployMode(ctx)
		agentcorpuslog.Info("defaulted deployMode", "name", r.Name, "deployMode", r.Spec.DeployMode)
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
		r.Spec.Observability.Backend = MetricsBackendOTel
	}
	// When OTel is selected but no collector endpoint is given, point agents at
	// the in-cluster Collector the operator deploys (<name>-otel-collector:4317),
	// so observability.backend=otel works out of the box (and the validator,
	// which requires otelCollector.endpoint for otel, passes).
	if r.Spec.Observability.Backend == MetricsBackendOTel && r.Spec.Observability.OTelCollector == nil {
		r.Spec.Observability.OTelCollector = &OTelCollectorSpec{
			Endpoint: fmt.Sprintf("%s-otel-collector:4317", r.Name),
		}
	}
	if r.Spec.UpgradePolicy.Mode == "" {
		r.Spec.UpgradePolicy.Mode = UpgradeModeAuto
	}
	if r.Spec.ManifestDelivery == "" {
		r.Spec.ManifestDelivery = "all"
	}
	for i := range r.Spec.MCPServers {
		mcp := &r.Spec.MCPServers[i]
		if mcp.Replicas == 0 {
			mcp.Replicas = 1
		}
		if mcp.Port == 0 {
			mcp.Port = 8080
		}
	}
	return nil
}

// detectDeployMode returns "rhoai" when a DataScienceCluster exists on the
// cluster (RHOAI / OpenShift AI is installed), else "standalone". Detection
// failures fall back to "standalone" so admission never blocks on a probe.
func (d *AgentCorpusCustomDefaulter) detectDeployMode(ctx context.Context) DeployMode {
	if d.Client == nil {
		return DeployModeStandalone
	}
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group:   "datasciencecluster.opendatahub.io",
		Version: "v1",
		Kind:    "DataScienceClusterList",
	})
	if err := d.Client.List(ctx, list); err != nil {
		agentcorpuslog.Info("RHOAI detection: DataScienceCluster list failed; defaulting standalone",
			"error", err.Error())
		return DeployModeStandalone
	}
	if len(list.Items) > 0 {
		return DeployModeRHOAI
	}
	return DeployModeStandalone
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

	// duplicate MCP server names not allowed; the reconciler in PR-51 derives
	// the Service name from MCPServerSpec.Name, so collisions would be fatal.
	mcpSeen := map[string]bool{}
	for i, m := range r.Spec.MCPServers {
		if mcpSeen[m.Name] {
			allErrs = append(allErrs, field.Invalid(
				field.NewPath("spec", "mcpServers").Index(i).Child("name"),
				m.Name,
				fmt.Sprintf("duplicate MCP server name %q", m.Name),
			))
		}
		mcpSeen[m.Name] = true
	}

	if len(allErrs) == 0 {
		return nil
	}
	return apierrors.NewInvalid(
		schema.GroupKind{Group: "acc.redhat.io", Kind: "AgentCorpus"},
		r.Name, allErrs,
	)
}
