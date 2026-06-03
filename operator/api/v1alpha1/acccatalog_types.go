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

// AccCatalogSpec declares one entry in the layered catalog list
// rendered to /etc/acc/catalogs.yaml inside ACC pods (Stage 1.6).
//
// Mirrors acc/pkg/catalog.py's Catalog Pydantic model so the YAML
// the operator emits validates cleanly against the Python loader.
// Operator reconciler watches all AccCatalog resources in the
// namespace and renders one ConfigMap acc-catalogs per namespace
// containing the merged YAML.
//
// +kubebuilder:object:generate=true
type AccCatalogSpec struct {
	// CatalogID is the human-readable identifier surfaced in the
	// Compliance pane's "alternates" column.  Must be unique within
	// the namespace.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=63
	CatalogID string `json:"catalogId"`

	// Tier is the trust classification (per brainstorm Q3b).
	// +kubebuilder:validation:Enum=trusted;tp;community;self
	Tier string `json:"tier"`

	// Mode selects the index source.
	// +kubebuilder:validation:Enum=https;file
	Mode string `json:"mode"`

	// URL is the HTTPS endpoint (mode=https only).
	// +optional
	URL string `json:"url,omitempty"`

	// Path is the on-disk directory (mode=file only); typically a
	// PVC mount point in the ACC pod.
	// +optional
	Path string `json:"path,omitempty"`

	// RequiredSigner pins the cosign identity catalog entries must
	// match.  Drives the signing-floor check at install time.
	RequiredSigner CatalogRequiredSigner `json:"requiredSigner"`

	// Priority breaks ties within a layer when two catalogs
	// advertise the same @scope/name; higher wins.
	// +kubebuilder:default=100
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=1000
	// +optional
	Priority int32 `json:"priority,omitempty"`
}

// CatalogRequiredSigner mirrors acc.pkg.catalog.RequiredSigner.
// +kubebuilder:object:generate=true
type CatalogRequiredSigner struct {
	// Issuer is the OIDC issuer URL (keyless) or a free-form audit
	// label (keypair mode).
	// +kubebuilder:validation:MinLength=1
	Issuer string `json:"issuer"`

	// SubjectPattern is the regex that the cert subject must match.
	// Validated by the operator's admission webhook (re.compile-style).
	// +kubebuilder:validation:MinLength=1
	SubjectPattern string `json:"subjectPattern"`

	// KeyPath switches to keypair-mode verification — points at a
	// cosign public-key PEM file on the pod's filesystem.
	// +optional
	KeyPath string `json:"keyPath,omitempty"`
}

// AccCatalogStatus carries observed-state fields the controller
// populates so kubectl get / GitOps tooling can show readiness.
// +kubebuilder:object:generate=true
type AccCatalogStatus struct {
	// ObservedGeneration matches metadata.generation when the
	// controller last reconciled.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions list the standard Ready / Reconciled conditions.
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// LastRenderedAt is when the operator last wrote the catalogs
	// ConfigMap from this resource.
	// +optional
	LastRenderedAt *metav1.Time `json:"lastRenderedAt,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=acccatalog
// +kubebuilder:printcolumn:name="Tier",type=string,JSONPath=".spec.tier"
// +kubebuilder:printcolumn:name="Mode",type=string,JSONPath=".spec.mode"
// +kubebuilder:printcolumn:name="Priority",type=integer,JSONPath=".spec.priority"
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=".metadata.creationTimestamp"

// AccCatalog is the Schema for the acccatalogs API.
type AccCatalog struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   AccCatalogSpec   `json:"spec,omitempty"`
	Status AccCatalogStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// AccCatalogList contains a list of AccCatalog.
type AccCatalogList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []AccCatalog `json:"items"`
}

func init() {
	SchemeBuilder.Register(&AccCatalog{}, &AccCatalogList{})
}
