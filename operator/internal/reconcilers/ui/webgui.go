// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package ui holds the reconcilers for ACC's human interaction surfaces
// (proposal 023 / ADR 025 interaction plane): acc-webgui (here) and, later,
// acc-tui.
package ui

import (
	"context"
	"fmt"
	"sort"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	ctrllog "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const (
	webguiComponent = "webgui"
	webguiPort      = 8080 // acc-webgui (bound to loopback; proxy is sole ingress)
	proxyPort       = 4180 // oauth2-proxy front (the Service + Route target)
	// oauth2ProxyImage is the pinned Keycloak-capable OIDC proxy. Bump
	// deliberately; mirrored into the operator's chosen registry for
	// disconnected installs.
	oauth2ProxyImage = "quay.io/oauth2-proxy/oauth2-proxy:v7.6.0"
)

var routeGVK = schema.GroupVersionKind{
	Group:   "route.openshift.io",
	Version: "v1",
	Kind:    "Route",
}

// WebGUIReconciler deploys acc-webgui behind a Keycloak-OIDC oauth2-proxy
// sidecar (proposal 023 / ADR 025). It refuses to expose an unauthenticated
// surface: an enabled WebGUI without a Keycloak block is a no-op + a logged
// block reason (ADR 025 §5).
type WebGUIReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *WebGUIReconciler) Name() string { return "ui/webgui" }

// Reconcile implements SubReconciler.
func (r *WebGUIReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	log := ctrllog.FromContext(ctx).WithName("webgui")
	spec := corpus.Spec.WebGUI

	// Gate: not requested, or explicitly disabled.
	if spec == nil || (spec.Enabled != nil && !*spec.Enabled) {
		corpus.Status.WebGUIDeployed = false
		return reconcilers.SubResult{}, nil
	}

	// Fail-safe: never stand up an unauthenticated network surface (ADR 025 §5).
	if spec.Keycloak == nil || spec.Keycloak.IssuerURL == "" ||
		spec.Keycloak.ClientID == "" || spec.Keycloak.ClientSecretName == "" {
		corpus.Status.WebGUIDeployed = false
		log.Info("webgui enabled but Keycloak config is incomplete — NOT deploying " +
			"(ADR 025 §5: no unauthenticated surface). Set spec.webgui.keycloak " +
			"{issuerURL, clientID, clientSecretName}.")
		return reconcilers.SubResult{}, nil
	}

	ns := corpus.Namespace
	name := fmt.Sprintf("%s-webgui", corpus.Name)
	labels := util.CommonLabels(corpus.Name, webguiComponent, corpus.Spec.Version)

	// Service — fronts the oauth2-proxy port only.
	svc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns, Labels: labels},
		Spec: corev1.ServiceSpec{
			Selector: util.SelectorLabels(labels),
			Ports:    []corev1.ServicePort{{Name: "http", Port: proxyPort, TargetPort: intstr.FromInt(proxyPort)}},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, svc, func(existing client.Object) error {
		existing.(*corev1.Service).Spec.Ports = svc.Spec.Ports
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert webgui Service: %w", err)
	}

	// Deployment — webgui (loopback) + oauth2-proxy (Keycloak OIDC) sidecar.
	// Gather the corpus's collective ids so the webgui's NATSObserver
	// connects to the right NATS and subscribes to the right subjects.
	// (Crash fix: without ACC_NATS_URL / ACC_COLLECTIVE_IDS the webgui
	// fell back to a default that can't resolve → NoServersError →
	// "Application startup failed. Exiting." crash-loop.)
	collectiveIDs := r.collectiveIDs(ctx, corpus)
	deploy := r.buildDeployment(corpus, name, labels, collectiveIDs)
	result, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, deploy, func(existing client.Object) error {
		ed := existing.(*appsv1.Deployment)
		ed.Spec.Replicas = deploy.Spec.Replicas
		ed.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert webgui Deployment: %w", err)
	}

	// Route — discovery-gated + best-effort (proposal 031 §11 #3): a Route
	// failure (route.openshift.io absent on non-OpenShift, or an RBAC/transient
	// API error) must NOT abort the whole corpus reconcile — the Service is
	// still reachable (expose it yourself / via GitOps). Log and continue.
	routeEnabled := spec.Route == nil || *spec.Route
	if routeEnabled {
		if err := r.upsertRoute(ctx, corpus, name, labels); err != nil {
			if apimeta.IsNoMatchError(err) {
				log.Info("route.openshift.io not present — skipping webgui Route (expose the Service yourself)")
			} else {
				log.Error(err, "webgui Route upsert failed; continuing without an operator-managed Route (expose the Service yourself)")
			}
		}
		// Surface the URL + an app-launcher ConsoleLink once the Route is
		// admitted (best-effort, discovery-gated, non-fatal) so the WebGUI
		// shows up as an independent menu item (operator review 2026-06-16).
		if host := routeHost(ctx, r.Client, ns, name); host != "" {
			corpus.Status.WebGUIURL = "https://" + host
			if err := upsertConsoleLink(ctx, r.Client, corpus,
				fmt.Sprintf("acc-%s-webgui", corpus.Name),
				fmt.Sprintf("ACC WebGUI — %s", corpus.Name),
				corpus.Status.WebGUIURL,
			); err != nil && !apimeta.IsNoMatchError(err) {
				log.Error(err, "webgui ConsoleLink upsert failed (non-fatal)")
			}
		}
	}

	corpus.Status.WebGUIDeployed = true
	// Requeue while the Route host isn't admitted yet so the URL +
	// ConsoleLink get captured on a later pass (a Ready corpus otherwise
	// stops reconciling before the host appears).
	progressing := result != util.UpsertResultNoop
	if routeEnabled && corpus.Status.WebGUIURL == "" {
		progressing = true
	}
	return reconcilers.SubResult{Progressing: progressing}, nil
}

func (r *WebGUIReconciler) buildDeployment(corpus *accv1alpha1.AgentCorpus, name string, labels map[string]string, collectiveIDs []string) *appsv1.Deployment {
	spec := corpus.Spec.WebGUI
	replicas := spec.Replicas
	if replicas == 0 {
		replicas = 1
	}
	groupsClaim := spec.Keycloak.GroupsClaim
	if groupsClaim == "" {
		groupsClaim = "groups"
	}
	secretEnv := func(key string) *corev1.EnvVarSource {
		return &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{Name: spec.Keycloak.ClientSecretName},
			Key:                  key,
		}}
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: corpus.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(replicas),
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					SecurityContext:  &corev1.PodSecurityContext{RunAsNonRoot: ptr.To(true)},
					Containers: []corev1.Container{
						{
							// acc-webgui — bound to loopback; the proxy is the
							// sole ingress (shared pod network namespace).
							Name:  "webgui",
							Image: util.ComponentImage(corpus, "acc-webgui", corpus.Spec.Version),
							Env: []corev1.EnvVar{
								{Name: "ACC_WEBGUI_HOST", Value: "127.0.0.1"},
								{Name: "ACC_WEBGUI_PORT", Value: fmt.Sprintf("%d", webguiPort)},
								{Name: "ACC_WEBGUI_AUTH_MODE", Value: "oauth-proxy"},
								{Name: "ACC_WEBGUI_OIDC_GROUPS_CLAIM", Value: groupsClaim},
								{Name: "ACC_WEBGUI_GROUP_MAPPINGS", Value: renderGroupMappings(spec.GroupMappings)},
								// Crash fix — the FastAPI lifespan starts a
								// NATSObserver per collective; without these it
								// fell back to localhost/sol-01 (gaierror →
								// NoServersError → startup crash-loop). Point it
								// at the corpus NATS + the observed collectives.
								{Name: "ACC_NATS_URL", Value: fmt.Sprintf("nats://%s-nats:4222", corpus.Name)},
								{Name: "ACC_CORPUS_NAME", Value: corpus.Name},
								{Name: "ACC_COLLECTIVE_IDS", Value: strings.Join(collectiveIDs, ",")},
							},
						},
						{
							// oauth2-proxy — runs the Keycloak OIDC auth-code
							// flow, forwards identity + groups to the webgui.
							Name:  "oauth2-proxy",
							Image: oauth2ProxyImage,
							Args: []string{
								"--provider=keycloak-oidc",
								"--oidc-issuer-url=" + spec.Keycloak.IssuerURL,
								"--client-id=" + spec.Keycloak.ClientID,
								"--client-secret=$(CLIENT_SECRET)",
								"--cookie-secret=$(COOKIE_SECRET)",
								"--email-domain=*",
								fmt.Sprintf("--upstream=http://127.0.0.1:%d", webguiPort),
								"--pass-user-headers=true",
								"--set-xauthrequest=true",
								"--scope=openid email groups",
								fmt.Sprintf("--http-address=0.0.0.0:%d", proxyPort),
							},
							Env: []corev1.EnvVar{
								{Name: "CLIENT_SECRET", ValueFrom: secretEnv("client-secret")},
								{Name: "COOKIE_SECRET", ValueFrom: secretEnv("cookie-secret")},
							},
							Ports: []corev1.ContainerPort{{Name: "http", ContainerPort: proxyPort}},
						},
					},
				},
			},
		},
	}
}

func (r *WebGUIReconciler) upsertRoute(ctx context.Context, corpus *accv1alpha1.AgentCorpus, name string, labels map[string]string) error {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(routeGVK)
	u.SetName(name)
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(labels)
	spec := map[string]interface{}{
		"to":   map[string]interface{}{"kind": "Service", "name": name},
		"port": map[string]interface{}{"targetPort": int64(proxyPort)},
		"tls":  map[string]interface{}{"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"},
	}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	_, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, u, func(existing client.Object) error {
		eu := existing.(*unstructured.Unstructured)
		return unstructured.SetNestedMap(eu.Object, spec, "spec")
	})
	return err
}

// collectiveIDs resolves the CollectiveID of every AgentCollective the
// corpus references, so the webgui's NATSObserver subscribes to the
// right subjects. Best-effort: an unresolvable collective is skipped
// rather than blocking the webgui (it can still serve the UI + the
// collectives that do resolve).
func (r *WebGUIReconciler) collectiveIDs(ctx context.Context, corpus *accv1alpha1.AgentCorpus) []string {
	ids := make([]string, 0, len(corpus.Spec.Collectives))
	for _, ref := range corpus.Spec.Collectives {
		c := &accv1alpha1.AgentCollective{}
		if err := r.Client.Get(ctx, client.ObjectKey{
			Namespace: corpus.Namespace, Name: ref.Name,
		}, c); err != nil {
			continue
		}
		if c.Spec.CollectiveID != "" {
			ids = append(ids, c.Spec.CollectiveID)
		}
	}
	return ids
}

// renderGroupMappings turns the tier→group map into the
// ACC_WEBGUI_GROUP_MAPPINGS wire string ("operator=...;publisher=..."),
// sorted for a deterministic pod template (no reconcile churn).
func renderGroupMappings(m map[string]string) string {
	if len(m) == 0 {
		return ""
	}
	tiers := make([]string, 0, len(m))
	for tier := range m {
		tiers = append(tiers, tier)
	}
	sort.Strings(tiers)
	parts := make([]string, 0, len(tiers))
	for _, tier := range tiers {
		if g := strings.TrimSpace(m[tier]); g != "" {
			parts = append(parts, tier+"="+g)
		}
	}
	return strings.Join(parts, ";")
}
