// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// AccPackageInstallSpec declares a single @scope/name@constraint
// package to be installed in this namespace's ACC pods (Stage 1.6).
//
// The reconciler periodically:
//
//  1. Picks an AccCorpus / AccCollective pod in the namespace.
//  2. Exec's `acc-cli collective pkg-install ... --json` against a
//     synthesised collective.yaml whose required_packages: list
//     mirrors the active AccPackageInstall resources.
//  3. Records status (installed/failed/missing) on the CR.
//
// This is the GitOps seam for Stage 1.5.3's pkg-install code path:
// ArgoCD or Flux drops AccPackageInstall objects into the cluster
// and the operator reconciles them onto live ACC pods.
//
// +kubebuilder:object:generate=true
type AccPackageInstallSpec struct {
	// Name is the scoped package to install, in @scope/name form (for example
	// "@acc/capital-markets-roles"). Lowercase; the @scope prefix is
	// mandatory. Resolved against the AccCatalog layers in this namespace
	// (optionally pinned with catalogRef).
	// +kubebuilder:validation:Pattern=`^@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_-]*$`
	Name string `json:"name"`

	// Constraint is the semver range to resolve: an exact version ("0.1.0"),
	// a caret range ("^1.2"), a tilde range ("~1.2.3"), or a bounded range
	// (">=1.2 <2.0"). The acc-pkg installer resolves the highest matching
	// version from the catalog; a version with no match fails the install
	// with EXIT_DEPS. LEAVE EMPTY to install the latest published version
	// (the resolver picks the highest version from the highest-priority
	// catalog). Pin a constraint in production for reproducible installs.
	// +optional
	Constraint string `json:"constraint,omitempty"`

	// CatalogRef optionally pins the catalogId of the AccCatalog this package
	// must come from (for example "acc-canonical"). Leave empty to let the
	// layered resolver pick the highest-priority catalog that provides the
	// package.
	// +optional
	CatalogRef string `json:"catalogRef,omitempty"`

	// TargetCorpus optionally names the AgentCorpus whose pods receive this
	// install (for example "finance-demo"). Leave empty to install into every
	// AgentCorpus in this namespace.
	// +optional
	TargetCorpus string `json:"targetCorpus,omitempty"`

	// AllowUnsigned bypasses the catalog's cosign signing floor for THIS
	// install only. Operator-explicit and audit-logged — leave false (the
	// default) in production; set true only for local or unsigned development
	// packages.
	// +optional
	// +kubebuilder:default=false
	AllowUnsigned bool `json:"allowUnsigned,omitempty"`
}

// AccPackageInstallStatus carries observed-state fields populated
// by the controller's reconcile loop.
// +kubebuilder:object:generate=true
type AccPackageInstallStatus struct {
	// ObservedGeneration matches metadata.generation when the
	// controller last reconciled.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Phase is a high-level summary surfaced by `kubectl get`.
	// +kubebuilder:validation:Enum=Pending;Installing;Installed;Failed
	// +optional
	Phase string `json:"phase,omitempty"`

	// InstalledVersion is the exact semver the resolver chose; empty
	// until first successful install.
	// +optional
	InstalledVersion string `json:"installedVersion,omitempty"`

	// InstallPath is the unpacked tree location on the target pod.
	// +optional
	InstallPath string `json:"installPath,omitempty"`

	// ContentSHA256 is the content-tree hash recorded in the
	// registry; lets GitOps tooling detect content drift.
	// +optional
	ContentSHA256 string `json:"contentSha256,omitempty"`

	// LastInstalledAt records the most recent successful install
	// (idempotent re-installs update this).
	// +optional
	LastInstalledAt *metav1.Time `json:"lastInstalledAt,omitempty"`

	// Conditions list the standard Ready / Reconciled conditions.
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=accpkginstall
// +kubebuilder:printcolumn:name="Package",type=string,JSONPath=".spec.name"
// +kubebuilder:printcolumn:name="Constraint",type=string,JSONPath=".spec.constraint"
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=".status.phase"
// +kubebuilder:printcolumn:name="Installed",type=string,JSONPath=".status.installedVersion"
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=".metadata.creationTimestamp"

// AccPackageInstall is the Schema for the accpackageinstalls API.
type AccPackageInstall struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   AccPackageInstallSpec   `json:"spec,omitempty"`
	Status AccPackageInstallStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// AccPackageInstallList contains a list of AccPackageInstall.
type AccPackageInstallList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []AccPackageInstall `json:"items"`
}

func init() {
	SchemeBuilder.Register(&AccPackageInstall{}, &AccPackageInstallList{})
}
