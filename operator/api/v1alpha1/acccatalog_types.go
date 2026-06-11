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
	// CatalogID is the unique, human-readable identifier for this catalog
	// layer (for example "acc-canonical" or "acme-internal"). It is surfaced
	// in the Compliance pane's package "alternates" column and in acc-pkg
	// output. Must be unique within the namespace.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=63
	CatalogID string `json:"catalogId"`

	// Tier is the trust classification that orders catalog layers during
	// package resolution (a higher-trust tier wins ties). One of: "trusted"
	// (ACC-curated, fully vetted), "tp" (verified third-party partner),
	// "community" (self-attested community publisher), or "self" (your own
	// private or local catalog). Higher tiers usually pin a stricter
	// requiredSigner.
	// +kubebuilder:validation:Enum=trusted;tp;community;self
	Tier string `json:"tier"`

	// Mode selects where this catalog's index is read from: "https" fetches
	// index.json from a remote URL (set the url field); "file" reads it from
	// an on-disk directory (set the path field, typically a mounted PVC).
	// Use https for the public ACC ecosystem catalog and file for air-gapped
	// or self-hosted catalogs.
	// +kubebuilder:validation:Enum=https;file
	Mode string `json:"mode"`

	// URL is the HTTPS endpoint serving this catalog's index.json (for
	// example "https://flg77.github.io/acc-ecosystem"). Required when
	// mode=https; ignored when mode=file.
	// +optional
	URL string `json:"url,omitempty"`

	// Path is the on-disk directory holding this catalog's index (for example
	// "/var/lib/acc/catalogs/acme"), typically a PVC mount inside the ACC
	// pod. Required when mode=file; ignored when mode=https.
	// +optional
	Path string `json:"path,omitempty"`

	// RequiredSigner pins the cosign signing identity that every package from
	// this catalog must satisfy. It is the signing floor enforced at
	// acc-pkg install time — packages whose signature does not match are
	// rejected unless the AccPackageInstall explicitly sets allowUnsigned.
	RequiredSigner CatalogRequiredSigner `json:"requiredSigner"`

	// Priority breaks ties when two catalogs in the same tier advertise the
	// same @scope/name package — the higher number wins. Defaults to 100;
	// raise it to make this catalog override its peers.
	// +kubebuilder:default=100
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=1000
	// +optional
	Priority int32 `json:"priority,omitempty"`
}

// CatalogRequiredSigner mirrors acc.pkg.catalog.RequiredSigner.
// +kubebuilder:object:generate=true
type CatalogRequiredSigner struct {
	// Issuer is the expected OIDC token issuer for keyless (Fulcio)
	// signatures. The default matches the public ACC ecosystem catalog,
	// whose packages are signed by its GitHub Actions release workflow —
	// keep it unless you run your own catalog. In keypair mode it is a
	// free-form audit label describing the key owner.
	// +kubebuilder:default="https://token.actions.githubusercontent.com"
	// +optional
	Issuer string `json:"issuer,omitempty"`

	// SubjectPattern is a regular expression the signing certificate's
	// subject must match. The default pins the public ACC ecosystem
	// repository's release identity — change it when pointing at your own
	// catalog. Anchored, RE2/Python-re style; validated by the operator's
	// admission webhook. To install unsigned or self-signed development
	// packages instead, set allowUnsigned on the AccPackageInstall
	// (operator-explicit, audit-logged, at your own risk).
	// +kubebuilder:default="^https://github.com/flg77/acc-ecosystem/.*"
	// +optional
	SubjectPattern string `json:"subjectPattern,omitempty"`

	// KeyPath switches verification to keypair mode: an absolute path to a
	// cosign public-key PEM file mounted in the ACC pod (for example
	// "/etc/acc/keys/acme.pub"). Leave empty to use keyless (Fulcio/Rekor)
	// verification via issuer + subjectPattern.
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
