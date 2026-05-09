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
	"strings"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/validation/field"
	ctrl "sigs.k8s.io/controller-runtime"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/webhook"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/rolecatalogue"
)

var agentcollectivelog = logf.Log.WithName("agentcollective-resource")

// SetupWebhookWithManager registers the webhook with the Manager.
func (r *AgentCollective) SetupWebhookWithManager(mgr ctrl.Manager) error {
	return ctrl.NewWebhookManagedBy(mgr).
		For(r).
		Complete()
}

// +kubebuilder:webhook:path=/mutate-acc-redhat-io-v1alpha1-agentcollective,mutating=true,failurePolicy=fail,sideEffects=None,groups=acc.redhat.io,resources=agentcollectives,verbs=create;update,versions=v1alpha1,name=magentcollective.kb.io,admissionReviewVersions=v1

var _ webhook.Defaulter = &AgentCollective{}

// Default implements webhook.Defaulter to set default values.
func (r *AgentCollective) Default() {
	agentcollectivelog.Info("default", "name", r.Name)

	if r.Spec.HeartbeatIntervalSeconds == 0 {
		r.Spec.HeartbeatIntervalSeconds = 30
	}
	for i := range r.Spec.Agents {
		if r.Spec.Agents[i].Replicas == 0 {
			r.Spec.Agents[i].Replicas = 1
		}
	}
	if r.Spec.LLM.EmbeddingModel == "" {
		r.Spec.LLM.EmbeddingModel = "all-MiniLM-L6-v2"
	}
}

// +kubebuilder:webhook:path=/validate-acc-redhat-io-v1alpha1-agentcollective,mutating=false,failurePolicy=fail,sideEffects=None,groups=acc.redhat.io,resources=agentcollectives,verbs=create;update,versions=v1alpha1,name=vagentcollective.kb.io,admissionReviewVersions=v1

var _ webhook.Validator = &AgentCollective{}

// ValidateCreate implements webhook.Validator.
func (r *AgentCollective) ValidateCreate() (admission.Warnings, error) {
	agentcollectivelog.Info("validate create", "name", r.Name)
	return nil, r.validateAgentCollective()
}

// ValidateUpdate implements webhook.Validator.
func (r *AgentCollective) ValidateUpdate(old runtime.Object) (admission.Warnings, error) {
	agentcollectivelog.Info("validate update", "name", r.Name)
	return nil, r.validateAgentCollective()
}

// ValidateDelete implements webhook.Validator.
func (r *AgentCollective) ValidateDelete() (admission.Warnings, error) {
	return nil, nil
}

// validateAgentCollective enforces semantic rules that the CRD schema can't
// express: role names must be present in the operator's compiled-in
// catalogue (see internal/rolecatalogue), per-role replica counts in
// scaling overrides must reference declared agents, etc.
func (r *AgentCollective) validateAgentCollective() error {
	var allErrs field.ErrorList

	// Roles in spec.agents[*] must be in the catalogue.
	declaredRoles := map[string]bool{}
	for i, a := range r.Spec.Agents {
		role := string(a.Role)
		declaredRoles[role] = true
		if !rolecatalogue.IsKnown(role) {
			allErrs = append(allErrs, field.Invalid(
				field.NewPath("spec", "agents").Index(i).Child("role"),
				role,
				unknownRoleMessage(role),
			))
		}
	}

	// Roles referenced in spec.scaling.roleScaling[*] must (a) be in the
	// catalogue, AND (b) appear in spec.agents — otherwise the scaling
	// override is dead config.
	if r.Spec.Scaling != nil {
		for i, rs := range r.Spec.Scaling.RoleScaling {
			role := string(rs.Role)
			if !rolecatalogue.IsKnown(role) {
				allErrs = append(allErrs, field.Invalid(
					field.NewPath("spec", "scaling", "roleScaling").Index(i).Child("role"),
					role,
					unknownRoleMessage(role),
				))
				continue
			}
			if !declaredRoles[role] {
				allErrs = append(allErrs, field.Invalid(
					field.NewPath("spec", "scaling", "roleScaling").Index(i).Child("role"),
					role,
					fmt.Sprintf("role %q is not declared in spec.agents — scaling override has no target", role),
				))
			}
			if rs.MaxReplicas > 0 && rs.MinReplicas > rs.MaxReplicas {
				allErrs = append(allErrs, field.Invalid(
					field.NewPath("spec", "scaling", "roleScaling").Index(i).Child("minReplicas"),
					rs.MinReplicas,
					fmt.Sprintf("minReplicas (%d) must not exceed maxReplicas (%d)", rs.MinReplicas, rs.MaxReplicas),
				))
			}
		}
	}

	// LLM backend wiring sanity — the schema enforces presence of the right
	// sub-struct via OpenAPI, but we cross-check that the chosen backend's
	// sub-struct is non-nil here so the controller never has to nil-guard.
	switch r.Spec.LLM.Backend {
	case LLMBackendOllama:
		if r.Spec.LLM.Ollama == nil {
			allErrs = append(allErrs, field.Required(
				field.NewPath("spec", "llm", "ollama"),
				"llm.ollama is required when llm.backend=ollama",
			))
		}
	case LLMBackendAnthropic:
		if r.Spec.LLM.Anthropic == nil {
			allErrs = append(allErrs, field.Required(
				field.NewPath("spec", "llm", "anthropic"),
				"llm.anthropic is required when llm.backend=anthropic",
			))
		}
	case LLMBackendVLLM:
		if r.Spec.LLM.VLLM == nil {
			allErrs = append(allErrs, field.Required(
				field.NewPath("spec", "llm", "vllm"),
				"llm.vllm is required when llm.backend=vllm",
			))
		}
	case LLMBackendLlamaStack:
		if r.Spec.LLM.LlamaStack == nil {
			allErrs = append(allErrs, field.Required(
				field.NewPath("spec", "llm", "llamaStack"),
				"llm.llamaStack is required when llm.backend=llama_stack",
			))
		}
	}

	if len(allErrs) == 0 {
		return nil
	}
	return apierrors.NewInvalid(
		schema.GroupKind{Group: "acc.redhat.io", Kind: "AgentCollective"},
		r.Name, allErrs,
	)
}

// unknownRoleMessage formats the error for a role that doesn't appear in
// the catalogue, including up to three closest matches by Levenshtein
// distance. The intent is to make typos obvious without listing all 47
// known roles inline.
func unknownRoleMessage(role string) string {
	suggestions := rolecatalogue.Suggest(role, 3)
	if len(suggestions) == 0 {
		return fmt.Sprintf(
			"role %q is not in the operator's known-roles catalogue; "+
				"add roles/%s/role.yaml to the source tree and rebuild the operator, "+
				"or pick one of the existing personas",
			role, role,
		)
	}
	return fmt.Sprintf(
		"role %q is not in the operator's known-roles catalogue; did you mean %s?",
		role, strings.Join(quoteAll(suggestions), ", "),
	)
}

func quoteAll(ss []string) []string {
	out := make([]string, len(ss))
	for i, s := range ss {
		out[i] = fmt.Sprintf("%q", s)
	}
	return out
}
