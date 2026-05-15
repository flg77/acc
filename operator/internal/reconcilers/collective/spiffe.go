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
	"strings"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// clusterSPIFFEIDGVK identifies the spire-controller-manager CRD that ACC
// issues per SPIFFE-enabled collective.  Proposal 011 PR-2.
var clusterSPIFFEIDGVK = schema.GroupVersionKind{
	Group:   "spire.spiffe.io",
	Version: "v1alpha1",
	Kind:    "ClusterSPIFFEID",
}

// SpiffeResult carries SPIFFE provisioning outcome back to the
// CollectiveReconciler so it can be written into AgentCollectiveStatus.
type SpiffeResult struct {
	// SpiffeID is the computed workload identity.  Empty when disabled.
	SpiffeID string
	// Issued is true when the ClusterSPIFFEID CR was created/updated.
	Issued bool
	// Err is an operator-readable reason when provisioning could not
	// complete (e.g. SPIRE absent).  Empty on success / when disabled.
	Err string
	// EdgeSiteID is the site qualifier baked into SpiffeID when the
	// collective runs deployMode=edge + edgeTopology=nested.  Empty
	// for non-edge / non-nested (proposal 012 PR-2).
	EdgeSiteID string
}

// +kubebuilder:rbac:groups=spire.spiffe.io,resources=clusterspiffeids;clusterfederatedtrustdomains,verbs=get;list;watch;create;update;patch;delete

// SpiffeReconciler issues a ClusterSPIFFEID custom resource per
// SPIFFE-enabled AgentCollective so spire-controller-manager attests
// the collective's agent pods.
//
// It is a strict no-op when:
//   - the collective's spec.spiffe is nil or spec.spiffe.enabled=false,
//   - spire-controller-manager is not installed (the spire.spiffe.io
//     API group is absent — detected by PrerequisiteReconciler and
//     surfaced via corpus.Status.Prerequisites.SpireInstalled).
//
// In neither case does it return an error — SPIFFE is opt-in and a
// missing SPIRE install must not break reconciliation.  The reason is
// reported via SpiffeResult.Err so the status block can show it.
//
// NOTE on ownership: ClusterSPIFFEID is cluster-scoped while
// AgentCollective is namespaced, so a controller owner-reference is
// impossible (cross-scope ownership is rejected by the API server).
// We therefore create the CR without an owner ref and tag it with the
// standard ACC labels; explicit cleanup on collective deletion is a
// follow-up (an orphaned ClusterSPIFFEID whose podSelector matches no
// pods is harmless — it simply attests nothing).
type SpiffeReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// ReconcileCollective issues (or removes) the ClusterSPIFFEID for one
// collective.
func (r *SpiffeReconciler) ReconcileCollective(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
) (SpiffeResult, error) {
	spiffe := collective.Spec.Spiffe

	// Disabled / absent — nothing to do.
	if spiffe == nil || !spiffe.Enabled {
		return SpiffeResult{}, nil
	}

	// SPIRE must be installed.  Detected once by PrerequisiteReconciler.
	if !corpus.Status.Prerequisites.SpireInstalled {
		return SpiffeResult{
			Err: "spiffe.enabled=true but spire-controller-manager " +
				"(spire.spiffe.io API group) is not installed; " +
				"falling back to ed25519",
		}, nil
	}

	trustDomain := r.resolveTrustDomain(corpus, spiffe)

	// Compute the SPIFFE ID.  Edge + nested topology qualifies the
	// path with the site ID so multiple edge sites under one trust
	// domain never collide (proposal 012 PR-2).
	spiffeID, edgeSiteID, err := r.computeSpiffeID(corpus, collective, spiffe, trustDomain)
	if err != nil {
		// Misconfiguration (e.g. nested topology without an edge_site_id)
		// — report it, don't fail reconciliation.
		return SpiffeResult{Err: err.Error()}, nil
	}

	desired := r.buildClusterSPIFFEID(corpus, collective, spiffeID)
	if _, err := util.Upsert(
		ctx, r.Client, r.Scheme,
		nil, // cluster-scoped — no namespaced owner ref (see type doc)
		desired,
		func(existing client.Object) error {
			desiredU := desired.(*unstructured.Unstructured)
			existingU := existing.(*unstructured.Unstructured)
			spec, _, _ := unstructured.NestedMap(desiredU.Object, "spec")
			return unstructured.SetNestedMap(existingU.Object, spec, "spec")
		},
	); err != nil {
		return SpiffeResult{SpiffeID: spiffeID, EdgeSiteID: edgeSiteID}, fmt.Errorf(
			"upsert ClusterSPIFFEID for %s: %w", collective.Name, err,
		)
	}

	// Edge + federated topology — issue a ClusterFederatedTrustDomain
	// per peer so this edge's SPIRE trusts SVIDs from the peer trust
	// domains (proposal 012 PR-3).
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge &&
		spiffe.EdgeTopology == "federated" {
		if err := r.reconcileFederation(ctx, corpus, collective, spiffe); err != nil {
			return SpiffeResult{
				SpiffeID: spiffeID, EdgeSiteID: edgeSiteID, Issued: true,
				Err: fmt.Sprintf("federation: %v", err),
			}, nil
		}
	}

	return SpiffeResult{
		SpiffeID:   spiffeID,
		EdgeSiteID: edgeSiteID,
		Issued:     true,
	}, nil
}

// reconcileFederation issues one ClusterFederatedTrustDomain custom
// resource per entry in spiffe.FederationPeers (proposal 012 PR-3).
//
// Each FederationPeers entry is a `<trust-domain>@<bundle-endpoint-url>`
// pair, e.g. `factory-b.acc.local@https://factory-b.example.com:8443/bundle`.
// A malformed entry (no `@`) is skipped with a returned error so the
// operator sees it in status.spiffeError — one bad peer does not block
// the others.
//
// The bundleEndpointProfile is `https_web` (standard web PKI on the
// peer's bundle endpoint) — the simplest profile.  Operators who run
// SPIFFE-authenticated bundle endpoints can switch the generated CR to
// `https_spiffe` by hand; documented in deploy/edge-spire/README.md.
func (r *SpiffeReconciler) reconcileFederation(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	spiffe *accv1alpha1.SpiffeSpec,
) error {
	if len(spiffe.FederationPeers) == 0 {
		return fmt.Errorf(
			"edgeTopology=federated requires at least one " +
				"spiffe.federationPeers entry",
		)
	}

	var malformed []string
	for _, peer := range spiffe.FederationPeers {
		td, url, ok := splitFederationPeer(peer)
		if !ok {
			malformed = append(malformed, peer)
			continue
		}
		ftd := r.buildFederatedTrustDomain(corpus, collective, td, url)
		if _, err := util.Upsert(
			ctx, r.Client, r.Scheme, nil, ftd,
			func(existing client.Object) error {
				desiredU := ftd.(*unstructured.Unstructured)
				existingU := existing.(*unstructured.Unstructured)
				spec, _, _ := unstructured.NestedMap(desiredU.Object, "spec")
				return unstructured.SetNestedMap(existingU.Object, spec, "spec")
			},
		); err != nil {
			return fmt.Errorf("upsert ClusterFederatedTrustDomain %s: %w", td, err)
		}
	}
	if len(malformed) > 0 {
		return fmt.Errorf(
			"federationPeers entries are not <trust-domain>@<url> pairs: %v",
			malformed,
		)
	}
	return nil
}

// splitFederationPeer parses a `<trust-domain>@<bundle-endpoint-url>`
// entry.  Returns ok=false when the `@` separator is absent or either
// half is blank.
func splitFederationPeer(peer string) (trustDomain, url string, ok bool) {
	at := strings.Index(peer, "@")
	if at < 0 {
		return "", "", false
	}
	td := strings.TrimSpace(peer[:at])
	u := strings.TrimSpace(peer[at+1:])
	if td == "" || u == "" {
		return "", "", false
	}
	return td, u, true
}

// sanitizeTrustDomain turns a trust domain into a DNS-label-safe
// fragment for use in a resource name (dots → dashes).
func sanitizeTrustDomain(td string) string {
	return strings.ReplaceAll(td, ".", "-")
}

func (r *SpiffeReconciler) buildFederatedTrustDomain(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	peerTrustDomain, bundleURL string,
) client.Object {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(schema.GroupVersionKind{
		Group:   "spire.spiffe.io",
		Version: "v1alpha1",
		Kind:    "ClusterFederatedTrustDomain",
	})
	u.SetName(fmt.Sprintf(
		"acc-%s-%s-fed-%s",
		corpus.Namespace, collective.Name, sanitizeTrustDomain(peerTrustDomain),
	))
	u.SetLabels(util.CollectiveLabels(
		corpus.Name, collective.Spec.CollectiveID,
		"spiffe-federation", corpus.Spec.Version,
	))
	spec := map[string]interface{}{
		"trustDomain":       peerTrustDomain,
		"bundleEndpointURL": bundleURL,
		"bundleEndpointProfile": map[string]interface{}{
			"type": "https_web",
		},
	}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	return u
}

// computeSpiffeID derives the workload SPIFFE ID for a collective.
//
// rhoai / standalone, or edge with a non-nested topology:
//
//	spiffe://<trust-domain>/role/<collective-name>
//
// edge + nested topology — qualified with the site ID:
//
//	spiffe://<trust-domain>/edge/<site-id>/role/<collective-name>
//
// Returns (spiffeID, edgeSiteID, error).  edgeSiteID is non-empty
// only in the nested-edge case.  An error is returned when
// edgeTopology=nested but edge_site_id is blank — the caller turns
// that into a SpiffeResult.Err (config problem, not a hard failure).
func (r *SpiffeReconciler) computeSpiffeID(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	spiffe *accv1alpha1.SpiffeSpec,
	trustDomain string,
) (spiffeID, edgeSiteID string, err error) {
	isEdge := corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge
	if isEdge && spiffe.EdgeTopology == "nested" {
		site := strings.TrimSpace(spiffe.EdgeSiteID)
		if site == "" {
			return "", "", fmt.Errorf(
				"spiffe.edgeTopology=nested requires spiffe.edgeSiteID " +
					"to be set (deployMode=edge)",
			)
		}
		return fmt.Sprintf(
			"spiffe://%s/edge/%s/role/%s", trustDomain, site, collective.Name,
		), site, nil
	}
	return fmt.Sprintf("spiffe://%s/role/%s", trustDomain, collective.Name), "", nil
}

// resolveTrustDomain returns the explicit spec value, or derives the
// default <corpus-name>.acc.local when the operator left it blank.
func (r *SpiffeReconciler) resolveTrustDomain(
	corpus *accv1alpha1.AgentCorpus,
	spiffe *accv1alpha1.SpiffeSpec,
) string {
	if td := strings.TrimSpace(spiffe.TrustDomain); td != "" {
		return td
	}
	return fmt.Sprintf("%s.acc.local", corpus.Name)
}

// clusterSPIFFEIDName is the deterministic name for the CR backing a
// given collective.  Cluster-scoped names must be globally unique, so
// we qualify with the corpus namespace.
func clusterSPIFFEIDName(corpus *accv1alpha1.AgentCorpus, collectiveName string) string {
	return fmt.Sprintf("acc-%s-%s", corpus.Namespace, collectiveName)
}

func (r *SpiffeReconciler) buildClusterSPIFFEID(
	corpus *accv1alpha1.AgentCorpus,
	collective *accv1alpha1.AgentCollective,
	spiffeID string,
) client.Object {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(clusterSPIFFEIDGVK)
	u.SetName(clusterSPIFFEIDName(corpus, collective.Name))
	u.SetLabels(util.CollectiveLabels(
		corpus.Name, collective.Spec.CollectiveID,
		"spiffe-id", corpus.Spec.Version,
	))

	spec := map[string]interface{}{
		// spiffeIDTemplate accepts a literal SPIFFE ID; we precompute
		// it rather than relying on spire-controller-manager template
		// variables so the value in our CRD status is authoritative.
		"spiffeIDTemplate": spiffeID,
		// Attest exactly the pods this collective owns.  The
		// acc.io/collective label is applied by AgentDeploymentReconciler.
		"podSelector": map[string]interface{}{
			"matchLabels": map[string]interface{}{
				"acc.io/collective": collective.Spec.CollectiveID,
			},
		},
	}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	return u
}

// DeleteClusterSPIFFEID removes the CR backing a collective.  Exposed
// for the collective-deletion cleanup path (wired in a follow-up; see
// the type doc on ownership).
func (r *SpiffeReconciler) DeleteClusterSPIFFEID(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	collectiveName string,
) error {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(clusterSPIFFEIDGVK)
	u.SetName(clusterSPIFFEIDName(corpus, collectiveName))
	if err := r.Client.Delete(ctx, u); err != nil {
		return client.IgnoreNotFound(err)
	}
	return nil
}

